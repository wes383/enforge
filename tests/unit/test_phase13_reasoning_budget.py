from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from dr_core.config import Settings
from dr_core.db.enums import (
    ClaimType,
    ClaimVerificationStatus,
    ProjectLanguage,
    ResearchDepth,
)
from dr_core.providers.errors import (
    ProviderReasoningTruncated,
    ProviderResponseError,
)
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.models import LLMResponse
from dr_core.research.claims import (
    ClaimAssessment,
    ClaimCandidate,
    review_claim_with_llm,
)
from dr_core.research.generation import target_section_range
from dr_core.research.models import ResearchBrief
from dr_core.research.planning import build_outline_with_llm, outline_token_budget
from dr_core.research.polish import DraftArticleSection, polish_draft_sections
from dr_core.research.usage import ReasoningBudgetLLM, UsageRecordingLLM


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Enforcement research",
        "topic": "Does enforcement improve compliance?",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def _provider(handler) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        "sk-test-key",
        base_url="https://llm.test/v1",
        model="fake-model",
        retry_policy=RetryPolicy(max_attempts=1),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _truncated_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "thinking about the outline ...",
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 800,
                "completion_tokens_details": {"reasoning_tokens": 790},
            },
        },
    )


class _TruncatingProvider:
    """A provider that spends the whole budget on reasoning."""

    name = "fake-reasoning-model"

    def __init__(self, *, responses: int = 1) -> None:
        self.calls: list[dict] = []
        self._responses = responses

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if len(self.calls) <= self._responses:
            raise ProviderReasoningTruncated(
                "openai",
                "chat completion returned an empty answer",
                input_tokens=120,
                output_tokens=800,
                reasoning_tokens=790,
            )
        return LLMResponse(
            text='{"sections": []}',
            model=self.name,
            input_tokens=120,
            output_tokens=400,
        )


class _EchoProvider:
    name = "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        return LLMResponse(text="{}", model=self.name)


def test_empty_answer_after_reasoning_is_reported_as_a_truncation():
    provider = _provider(lambda request: _truncated_response())

    with pytest.raises(ProviderReasoningTruncated) as excinfo:
        provider.generate("Design the outline.", max_tokens=800)

    error = excinfo.value
    assert error.code == "reasoning_truncated"
    assert error.retryable is False
    assert error.input_tokens == 120
    assert error.output_tokens == 800
    assert error.reasoning_tokens == 790
    assert "finish_reason=length" in str(error)


def test_empty_answer_without_reasoning_keeps_the_generic_error():
    provider = _provider(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
        )
    )

    with pytest.raises(ProviderResponseError) as excinfo:
        provider.generate("Design the outline.")

    assert not isinstance(excinfo.value, ProviderReasoningTruncated)


def test_truncated_attempt_is_billed_and_the_retry_uses_a_larger_budget():
    provider = _TruncatingProvider()
    recorded: list[object] = []
    llm = ReasoningBudgetLLM(
        UsageRecordingLLM(provider, recorded.append),
        factor=2,
        ceiling=16000,
    )

    response = llm.generate("Design the outline.", max_tokens=800)

    assert response.text == '{"sections": []}'
    assert response.retried_after_truncation is True
    assert [call["max_tokens"] for call in provider.calls] == [800, 1600]
    # the truncated attempt was paid for, and so was the successful one
    assert len(recorded) == 2
    assert isinstance(recorded[0], ProviderReasoningTruncated)
    assert recorded[1].text == response.text


def test_the_retry_stays_inside_the_configured_ceiling():
    provider = _TruncatingProvider(responses=1)
    llm = ReasoningBudgetLLM(provider, factor=4, ceiling=2000)

    llm.generate("Design the outline.", max_tokens=1000)

    assert [call["max_tokens"] for call in provider.calls] == [1000, 2000]


def test_a_truncation_without_a_budget_is_not_retried():
    provider = _TruncatingProvider()
    llm = ReasoningBudgetLLM(provider, factor=2, ceiling=16000)

    with pytest.raises(ProviderReasoningTruncated):
        llm.generate("Design the outline.")

    assert len(provider.calls) == 1


def test_a_retry_that_fails_again_is_reported_to_the_caller():
    provider = _TruncatingProvider(responses=2)
    notices: list[tuple[str, int, int]] = []
    llm = ReasoningBudgetLLM(
        provider,
        factor=2,
        ceiling=16000,
        notify=lambda message, spent, larger: notices.append((message, spent, larger)),
    )

    with pytest.raises(ProviderReasoningTruncated):
        llm.generate("Design the outline.", max_tokens=800)

    assert len(provider.calls) == 2
    assert notices and notices[0][1] == 800
    assert notices[0][2] == 1600


def test_the_outline_budget_has_a_floor_and_grows_with_the_article():
    brief = _brief(target_words=12000)
    low, _high = target_section_range(brief)

    assert outline_token_budget(brief, floor=6000, per_section=400) == max(
        6000,
        400 * low,
    )
    assert outline_token_budget(brief, floor=1000, per_section=10000) == 10000 * low
    # the floor is the minimum, never a maximum
    assert outline_token_budget(brief, floor=6000, per_section=400) >= 6000


def test_the_outline_call_uses_the_budget_the_caller_supplies():
    class _Recorder(_EchoProvider):
        pass

    provider = _Recorder()
    build_outline_with_llm(
        _brief(),
        provider,
        version=1,
        max_tokens=6400,
    )

    assert provider.calls[0]["max_tokens"] == 6400


def test_the_claim_review_budget_leaves_room_for_reasoning():
    provider = _EchoProvider()
    assessment = ClaimAssessment(
        candidate=ClaimCandidate(
            section_index=0,
            text="Enforcement raises compliance.",
            claim_type=ClaimType.CAUSAL,
            markers=("S1",),
        ),
        status=ClaimVerificationStatus.UNSUPPORTED,
    )
    evidence = [
        SimpleNamespace(
            claim_summary="Audits rose.",
            quoted_text="Audits rose by 12%.",
            context_before="",
            context_after="",
        )
    ]

    review_claim_with_llm(
        provider,
        assessment,
        evidence,
        max_tokens=1200,
    )

    assert provider.calls[0]["max_tokens"] == 1200


def test_the_polish_budget_keeps_a_floor_for_reasoning():
    provider = _EchoProvider()
    sections = [
        DraftArticleSection(index=0, title="Introduction", content="A" * 200),
        DraftArticleSection(index=1, title="Body", content="B" * 200),
    ]

    polish_draft_sections(
        brief=_brief(),
        sections=sections,
        llm=provider,
        max_input_chars=100000,
        max_output_tokens=4000,
    )

    assert provider.calls[0]["max_tokens"] == 4000


def test_the_call_budget_covers_the_repeated_request():
    settings = Settings(
        _env_file=None,
        llm_request_timeout_seconds=120,
        llm_max_retries=0,
    )
    assert settings.llm_call_budget_seconds == 240

    without_retry = settings.model_copy(update={"llm_reasoning_retry_factor": 1})
    assert without_retry.llm_call_budget_seconds == 120
