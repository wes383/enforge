from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from dr_core.providers.errors import ProviderResponseError
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import LLMResponse


class OpenAICompatibleLLMProvider:
    """Chat-completions adapter for OpenAI-compatible endpoints.

    The endpoint is operator-configured (any OpenAI-compatible server, including
    local vLLM/Ollama gateways). Requests carry only the research prompt and the
    provider key; responses are validated before the workflow consumes them.
    """

    def __init__(
        self,
        api_key: str | SecretStr | None,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 5.0,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = (
            api_key.get_secret_value()
            if isinstance(api_key, SecretStr)
            else (api_key or "")
        )
        self._model = model
        self._http = JsonHttpClient(
            "openai",
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            retry_policy=retry_policy,
            client=client,
        )

    @property
    def name(self) -> str:
        return self._model

    @property
    def enabled(self) -> bool:
        return True

    def close(self) -> None:
        self._http.close()

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        response = self._http.request_json(
            "POST",
            "/chat/completions",
            json=payload,
        )
        return _parse_completion(response, self._model)


def _parse_completion(payload: dict[str, Any], fallback_model: str) -> LLMResponse:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError(
            "openai",
            "chat completion response is missing choices",
        )
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseError(
            "openai",
            "chat completion response is missing message content",
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    return LLMResponse(
        text=content,
        model=str(payload.get("model") or fallback_model),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        finish_reason=(
            str(finish_reason)[:64] if isinstance(finish_reason, str) else None
        ),
    )
