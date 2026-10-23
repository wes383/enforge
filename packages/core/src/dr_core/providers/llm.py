from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from dr_core.providers.errors import (
    ProviderError,
    ProviderReasoningTruncated,
    ProviderResponseError,
)
from dr_core.providers.http import JsonHttpClient, RetryPolicy
from dr_core.providers.models import LLMResponse, LLMToolCall


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
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
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
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        raise ProviderResponseError(
            "openai",
            "chat completion response is missing the message object",
        )
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    reasoning = message.get("reasoning_content")
    reasoning_text = reasoning if isinstance(reasoning, str) else ""
    tool_calls = _parse_tool_calls(message)
    if not text.strip() and not tool_calls:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        raise _empty_answer_error(finish_reason, reasoning_text, usage)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return LLMResponse(
        text=text,
        model=str(payload.get("model") or fallback_model),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        finish_reason=(
            str(finish_reason)[:64] if isinstance(finish_reason, str) else None
        ),
        reasoning_text=reasoning_text[:8000] or None,
        reasoning_tokens=_reasoning_tokens(usage),
        tool_calls=tool_calls,
    )


def _empty_answer_error(
    finish_reason: Any,
    reasoning_text: str,
    usage: dict[str, Any],
) -> ProviderError:
    """Explain why the model returned no answer.

    A reasoning model that spent the whole token budget thinking returns an
    empty answer; that is a truncation and needs a different fix from an
    answer the model genuinely refused to produce.
    """
    reason = str(finish_reason) if isinstance(finish_reason, str) else "unknown"
    if reasoning_text.strip():
        message = (
            "chat completion returned an empty answer; the model used the "
            f"token budget for reasoning (finish_reason={reason})"
        )
    else:
        message = (
            "chat completion returned an empty answer "
            f"(finish_reason={reason})"
        )
    if not reasoning_text.strip() and reason != "length":
        return ProviderResponseError("openai", message)
    return ProviderReasoningTruncated(
        "openai",
        message,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=_reasoning_tokens(usage),
    )


def _reasoning_tokens(usage: dict[str, Any]) -> int:
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return 0
    try:
        return max(0, int(details.get("reasoning_tokens") or 0))
    except (TypeError, ValueError):
        return 0


def _parse_tool_calls(message: dict[str, Any]) -> list[LLMToolCall]:
    raw = message.get("tool_calls")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProviderResponseError("openai", "tool_calls must be a list")
    calls: list[LLMToolCall] = []
    for index, entry in enumerate(raw[:10]):
        function = entry.get("function") if isinstance(entry, dict) else None
        if not isinstance(function, dict):
            raise ProviderResponseError("openai", "tool call is missing its function")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProviderResponseError("openai", "tool call is missing a name")
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            encoded = json.dumps(arguments, ensure_ascii=False)
        elif isinstance(arguments, str):
            encoded = arguments or "{}"
        elif arguments is None:
            encoded = "{}"
        else:
            raise ProviderResponseError(
                "openai",
                "tool call arguments must be JSON text or an object",
            )
        call_id = entry.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"call_auto_{index}"
        calls.append(
            LLMToolCall(
                id=call_id[:200],
                name=name[:64],
                arguments=encoded[:20000],
            )
        )
    return calls
