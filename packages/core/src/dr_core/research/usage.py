from __future__ import annotations

from collections.abc import Callable

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
