from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from dr_core.db.enums import RunEventType, RunStatus
from dr_core.providers.base import LLMProvider
from dr_core.providers.models import LLMResponse

logger = logging.getLogger(__name__)


class UsageRecordingLLM:
    """Delegates generation and reports every successful call's usage."""

    def __init__(
        self,
        provider: LLMProvider,
        record: Callable[[LLMResponse], None],
    ) -> None:
        self._provider = provider
        self._record = record

    @property
    def name(self) -> str:
        return getattr(self._provider, "name", "llm")

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        response = self._provider.generate(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._record(response)
        return response

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        chat = getattr(self._provider, "chat", None)
        if chat is None:
            raise TypeError(f"{self.name} does not support tool calling")
        response = chat(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._record(response)
        return response


class ProgressReportingLLM:
    """Wraps a model and records a progress event around every call.

    The events are best-effort: a failure to persist one must never hide the
    model's own result or error.
    """

    def __init__(
        self,
        provider: LLMProvider,
        emit: Callable[..., None],
        *,
        stage: RunStatus,
        label: str,
        key_prefix: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._provider = provider
        self._emit_fn = emit
        self._stage = stage
        self._label = label
        self._key_prefix = key_prefix
        self._payload = payload or {}
        self._calls = 0

    @property
    def name(self) -> str:
        return getattr(self._provider, "name", "llm")

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        return self._call(
            "generate",
            lambda: self._provider.generate(
                prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        chat = getattr(self._provider, "chat", None)
        if chat is None:
            raise TypeError(f"{self.name} does not support tool calling")
        return self._call(
            "chat",
            lambda: chat(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    def _call(self, operation: str, call: Callable[[], LLMResponse]) -> LLMResponse:
        self._calls += 1
        number = self._calls
        payload = {
            **self._payload,
            "model_call": number,
            "operation": operation,
        }
        key = f"{self._key_prefix}:{number}"
        self._emit(
            message=f"{self._label} · model call {number} started",
            payload=payload,
            dedupe_key=f"{key}:started",
        )
        started = time.monotonic()
        try:
            response = call()
        except Exception as exc:
            self._emit(
                message=f"{self._label} · model call {number} failed",
                payload={
                    **payload,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
                dedupe_key=f"{key}:failed",
            )
            raise
        self._emit(
            message=f"{self._label} · model call {number} completed",
            payload={
                **payload,
                "input_tokens": max(0, int(response.input_tokens or 0)),
                "output_tokens": max(0, int(response.output_tokens or 0)),
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            dedupe_key=f"{key}:completed",
        )
        return response

    def _emit(
        self,
        *,
        message: str,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> None:
        try:
            self._emit_fn(
                RunEventType.PROGRESS,
                message=message,
                payload=payload,
                dedupe_key=dedupe_key,
                stage=self._stage,
            )
        except Exception:
            logger.warning("Unable to record LLM progress event", exc_info=True)
