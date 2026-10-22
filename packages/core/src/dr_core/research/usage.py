from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from dr_core.providers.base import LLMProvider
from dr_core.providers.models import LLMResponse


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
