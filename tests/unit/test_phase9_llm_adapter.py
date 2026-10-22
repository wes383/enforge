from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from dr_core.config import Settings
from dr_core.config.settings import LLM_SECTION_CALL_BUDGET
from dr_core.providers.errors import ProviderResponseError, ProviderUnavailable
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.registry import build_provider_registry
from dr_core.research.nodes import (
    _polish_node_timeout,
    _verification_node_timeout,
    _writing_node_timeout,
)


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


def test_chat_sends_tools_and_parses_tool_calls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "fake-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_sources",
                                        "arguments": json.dumps(
                                            {"query": "peer review"}
                                        ),
                                    },
                                },
                                {
                                    "function": {
                                        "name": "read_source",
                                        "arguments": {"source_id": "abc"},
                                    }
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            },
        )

    provider, requests = _provider(handler)
    messages = [
        {"role": "system", "content": "You are a researcher."},
        {"role": "user", "content": "Find sources."},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_sources",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    response = provider.chat(messages, tools=tools, max_tokens=128)

    assert response.text == ""
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "search_sources"
    assert json.loads(response.tool_calls[0].arguments) == {
        "query": "peer review"
    }
    assert response.tool_calls[1].id == "call_auto_1"
    assert response.tool_calls[1].name == "read_source"
    assert json.loads(response.tool_calls[1].arguments) == {"source_id": "abc"}
    assert response.input_tokens == 20

    body = json.loads(requests[0].content)
    assert body["messages"] == messages
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert body["max_tokens"] == 128


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


def test_llm_gets_its_own_timeout_and_retry_budget():
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-key",
        provider_request_timeout_seconds=20,
        provider_max_retries=3,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
    )
    registry = build_provider_registry(settings)
    try:
        http = registry.llm._http
        assert http._timeout.read == 120
        assert http._retry_policy.max_attempts == 1
    finally:
        registry.close()


def test_run_time_limits_expand_only_when_model_writing_is_on():
    offline = Settings(
        _env_file=None,
        run_task_soft_time_limit_seconds=300,
        run_task_hard_time_limit_seconds=360,
    )
    assert offline.effective_run_task_soft_time_limit_seconds == 300
    assert offline.effective_run_task_hard_time_limit_seconds == 360

    writing = Settings(
        _env_file=None,
        research_llm_writing_enabled=True,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
        research_claim_llm_review_limit=10,
        research_node_timeout_seconds=90,
    )
    assert writing.llm_call_budget_seconds == 120
    assert writing.effective_run_task_soft_time_limit_seconds == 120 * 34 + 90
    assert (
        writing.effective_run_task_hard_time_limit_seconds
        == writing.effective_run_task_soft_time_limit_seconds + 60
    )

    generous = Settings(
        _env_file=None,
        research_claim_llm_review=True,
        run_task_soft_time_limit_seconds=99999,
        run_task_hard_time_limit_seconds=100000,
    )
    assert generous.effective_run_task_soft_time_limit_seconds == 99999
    # the hard limit always stays above the effective soft limit
    assert generous.effective_run_task_hard_time_limit_seconds == 100059


def test_llm_node_timeouts_scale_with_expected_model_calls():
    settings = Settings(
        _env_file=None,
        research_llm_writing_enabled=True,
        research_claim_llm_review=True,
        research_claim_llm_review_limit=4,
        research_node_timeout_seconds=90,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
    )
    llm = OpenAICompatibleLLMProvider(
        "sk-test-key",
        model="fake-model",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            )
        ),
    )
    ctx = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=llm),
        record_usage=lambda response: None,
    )
    state = {"outline": {"sections": [{}, {}, {}]}}
    assert _writing_node_timeout(ctx, state) == 90 + 3 * 120
    assert _verification_node_timeout(ctx) == 90 + 4 * 120
    assert _writing_node_timeout(ctx, {}) == 90 + LLM_SECTION_CALL_BUDGET * 120

    deterministic = SimpleNamespace(
        settings=settings.model_copy(
            update={
                "research_llm_writing_enabled": False,
                "research_claim_llm_review": False,
            }
        ),
        registry=SimpleNamespace(llm=llm),
    )
    assert _writing_node_timeout(deterministic, state) == 90
    assert _verification_node_timeout(deterministic) == 90


def test_polish_adds_exactly_one_model_call_to_the_time_budgets():
    settings = Settings(
        _env_file=None,
        research_node_timeout_seconds=90,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
    )
    llm = OpenAICompatibleLLMProvider(
        "sk-test-key",
        model="fake-model",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={})
            )
        ),
    )
    offline = SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(llm=llm),
        record_usage=lambda response: None,
    )
    assert settings.llm_workflow_enabled is False
    assert _polish_node_timeout(offline) == 90

    polishing = SimpleNamespace(
        settings=settings.model_copy(update={"research_llm_polish_enabled": True}),
        registry=SimpleNamespace(llm=llm),
        record_usage=lambda response: None,
    )
    assert polishing.settings.llm_workflow_enabled is True
    assert _polish_node_timeout(polishing) == 90 + 120
    assert (
        polishing.settings.llm_run_time_floor_seconds
        - settings.llm_run_time_floor_seconds
        == 120
    )
