from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from dr_core.db.enums import RunEventType, RunStatus
from dr_core.providers.base import LLMProvider
from dr_core.providers.errors import ProviderReasoningTruncated
from dr_core.providers.models import LLMResponse

logger = logging.getLogger(__name__)


class UsageRecordingLLM:
    """Delegates generation and reports every successful call's usage."""

    def __init__(
        self,
        provider: LLMProvider,
        record: Callable[[Any], None],
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
        try:
            response = self._provider.generate(
                prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except ProviderReasoningTruncated as exc:
            # The attempt was paid for even though it produced no answer.
            self._record(exc)
            raise
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
        try:
            response = chat(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except ProviderReasoningTruncated as exc:
            self._record(exc)
            raise
        self._record(response)
        return response


class ReasoningBudgetLLM:
    """Repeats a call that the model spent entirely on reasoning.

    A reasoning model bills its thinking against the same limit as the answer,
    so a budget that is too small leaves nothing for the answer itself. The
    call is repeated once with a larger budget.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        factor: int = 2,
        ceiling: int = 16000,
        notify: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self._provider = provider
        self._factor = max(1, factor)
        self._ceiling = max(1, ceiling)
        self._notify = notify

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
        try:
            return self._provider.generate(
                prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except ProviderReasoningTruncated:
            larger = self._larger(max_tokens)
            if larger is None:
                raise
            self._report(max_tokens, larger)
            response = self._provider.generate(
                prompt,
                system=system,
                max_tokens=larger,
                temperature=temperature,
            )
            return response.model_copy(update={"retried_after_truncation": True})

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
        try:
            return chat(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except ProviderReasoningTruncated:
            larger = self._larger(max_tokens)
            if larger is None:
                raise
            self._report(max_tokens, larger)
            response = chat(
                messages,
                tools=tools,
                max_tokens=larger,
                temperature=temperature,
            )
            return response.model_copy(update={"retried_after_truncation": True})

    def _larger(self, max_tokens: int | None) -> int | None:
        if not max_tokens:
            return None
        larger = min(max_tokens * self._factor, self._ceiling)
        return larger if larger > max_tokens else None

    def _report(self, spent: int | None, larger: int) -> None:
        logger.info(
            "The model spent the token budget on reasoning; retrying with %s "
            "tokens instead of %s",
            larger,
            spent,
        )
        if self._notify is None:
            return
        try:
            self._notify(
                "The model spent the whole token budget on reasoning; retrying "
                f"with {larger} tokens instead of {spent}.",
                int(spent or 0),
                larger,
            )
        except Exception:
            logger.warning("Unable to record the reasoning retry", exc_info=True)


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
                "reasoning_tokens": max(0, int(response.reasoning_tokens or 0)),
                "finish_reason": response.finish_reason,
                "retried_after_truncation": bool(
                    getattr(response, "retried_after_truncation", False)
                ),
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
