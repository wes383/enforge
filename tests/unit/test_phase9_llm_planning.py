from __future__ import annotations

from types import SimpleNamespace

from dr_core.config import Settings
from dr_core.config.settings import (
    LLM_OUTLINE_CALL_BUDGET,
    LLM_RESEARCH_AGENT_CALL_BUDGET,
    LLM_SECTION_CALL_BUDGET,
)
from dr_core.providers.models import LLMResponse
from dr_core.research.nodes import (
    _evidence_agent_node_timeout,
    _llm_evidence_agent,
    _llm_outliner,
    _research_agent_node_timeout,
)
from dr_core.research.usage import UsageRecordingLLM


class _FakeLLM:
    name = "fake-model"

    def __init__(self):
        self.prompts: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(text="{}", model=self.name)

    def chat(self, messages, *, tools=None, **kwargs):
        return SimpleNamespace(text="{}", tool_calls=[])


class _MockLLM:
    name = "mock-llm"


def _settings(**overrides) -> Settings:
    values = {
        "llm_request_timeout_seconds": 120,
        "llm_max_retries": 1,
        "research_node_timeout_seconds": 90,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _ctx(settings, llm, *, search=False):
    return SimpleNamespace(
        settings=settings,
        registry=SimpleNamespace(
            llm=llm,
            web_search=[SimpleNamespace(enabled=search)],
            academic_search=[],
        ),
        record_usage=lambda response: None,
    )


def test_outline_fallback_gate_only_uses_a_real_enabled_model():
    settings = _settings(research_llm_outline_enabled=True)
    ctx = _ctx(settings, _FakeLLM())

    outliner = _llm_outliner(ctx)
    assert isinstance(outliner, UsageRecordingLLM)
    assert _research_agent_node_timeout(ctx) == (
        settings.research_node_timeout_seconds
        + settings.llm_call_budget_seconds
    )

    mock_only = _ctx(settings, _MockLLM())
    assert _llm_outliner(mock_only) is None
    assert (
        _research_agent_node_timeout(mock_only)
        == settings.research_node_timeout_seconds
    )

    disabled = _ctx(
        settings.model_copy(update={"research_llm_outline_enabled": False}),
        _FakeLLM(),
    )
    assert _llm_outliner(disabled) is None
    assert (
        _research_agent_node_timeout(disabled)
        == settings.research_node_timeout_seconds
    )


def test_evidence_agent_requires_tools_and_stays_inside_the_round_budget():
    settings = _settings(
        research_agent_enabled=True,
        research_llm_outline_enabled=True,
        research_agent_max_rounds=4,
    )
    ctx = _ctx(settings, _FakeLLM(), search=True)

    agent = _llm_evidence_agent(ctx)
    assert isinstance(agent, UsageRecordingLLM)
    expected = settings.research_node_timeout_seconds + (
        settings.research_agent_max_rounds + 1
    ) * settings.llm_call_budget_seconds
    assert _evidence_agent_node_timeout(ctx) == expected
    assert _research_agent_node_timeout(ctx) == expected

    without_search = _ctx(settings, _FakeLLM(), search=False)
    assert _llm_evidence_agent(without_search) is None
    assert (
        _evidence_agent_node_timeout(without_search)
        == settings.research_node_timeout_seconds
    )
    assert _research_agent_node_timeout(without_search) == (
        settings.research_node_timeout_seconds
        + settings.llm_call_budget_seconds
    )


def test_outline_and_agent_calls_expand_the_run_time_floor():
    baseline = _settings(
        research_llm_writing_enabled=True,
        run_task_soft_time_limit_seconds=300,
        run_task_hard_time_limit_seconds=360,
    )
    with_outline = baseline.model_copy(
        update={"research_llm_outline_enabled": True}
    )
    with_agent = with_outline.model_copy(
        update={"research_agent_enabled": True}
    )

    budget = baseline.llm_call_budget_seconds
    assert (
        with_outline.llm_run_time_floor_seconds
        - baseline.llm_run_time_floor_seconds
        == LLM_OUTLINE_CALL_BUDGET * budget
    )
    assert (
        with_agent.llm_run_time_floor_seconds
        - with_outline.llm_run_time_floor_seconds
        == LLM_RESEARCH_AGENT_CALL_BUDGET * budget
    )
    assert with_agent.effective_run_task_soft_time_limit_seconds > 300
    assert (
        with_agent.effective_run_task_hard_time_limit_seconds
        == with_agent.effective_run_task_soft_time_limit_seconds + 60
    )


def test_model_workflow_is_off_by_default():
    settings = Settings(_env_file=None)

    assert settings.research_llm_outline_enabled is False
    assert settings.research_agent_enabled is False
    assert settings.llm_workflow_enabled is False
    calls = LLM_SECTION_CALL_BUDGET + settings.research_claim_llm_review_limit
    assert settings.llm_run_time_floor_seconds == (
        settings.llm_call_budget_seconds * calls
        + settings.research_node_timeout_seconds
    )
