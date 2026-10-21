from __future__ import annotations

import json

import httpx
import pytest

from dr_core.config import Settings
from dr_core.providers.errors import ProviderResponseError, ProviderUnavailable
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.registry import build_provider_registry


def _provider(handler) -> tuple[OpenAICompatibleLLMProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider = OpenAICompatibleLLMProvider(
        "sk-test-key",
        base_url="https://llm.test/v1",
        model="fake-model",
        retry_policy=RetryPolicy(max_attempts=1),
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
    )
    return provider, requests


def test_generate_posts_chat_completion_and_parses_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "fake-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Hello world"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    provider, requests = _provider(handler)
    response = provider.generate(
        "Write about evidence.",
        system="You are a research writer.",
        max_tokens=64,
        temperature=0.2,
    )
    assert response.text == "Hello world"
    assert response.model == "fake-model"
    assert response.input_tokens == 12
    assert response.output_tokens == 3
    assert response.finish_reason == "stop"

    request = requests[0]
    assert request.url == "https://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test-key"
    body = json.loads(request.content)
    assert body["model"] == "fake-model"
    assert body["messages"][0] == {
        "role": "system",
        "content": "You are a research writer.",
    }
    assert body["messages"][1]["content"] == "Write about evidence."
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2


def test_generate_raises_on_http_and_malformed_payloads():
    failing, _ = _provider(lambda request: httpx.Response(500, json={}))
    with pytest.raises(ProviderUnavailable):
        failing.generate("prompt")

    malformed, _ = _provider(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ProviderResponseError):
        malformed.generate("prompt")

    empty_content, _ = _provider(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )
    )
    with pytest.raises(ProviderResponseError):
        empty_content.generate("prompt")


def test_registry_registers_llm_only_when_key_present():
    with_key = Settings(_env_file=None, openai_api_key="sk-test-key")
    registry = build_provider_registry(with_key)
    try:
        assert isinstance(registry.llm, OpenAICompatibleLLMProvider)
        assert registry.llm.name == with_key.openai_model
    finally:
        registry.close()

    without_key = Settings(_env_file=None, openai_api_key=None)
    assert build_provider_registry(without_key).llm is None
