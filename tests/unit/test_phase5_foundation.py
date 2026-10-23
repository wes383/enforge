from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dr_core.config import Settings
from dr_core.db.enums import (
    ApprovalStatus,
    ProjectLanguage,
    ResearchDepth,
    RunStatus,
)
from dr_core.db.models import ResearchRun
from dr_core.research.context import _budget_values
from dr_core.research.generation import build_plan
from dr_core.research.models import (
    ApprovalDecision,
    Outline,
    OutlineSection,
    ResearchBrief,
    ResearchGap,
    ResearchPlan,
    ResearchQuestion,
    SourceRecord,
)
from dr_core.research.nodes import (
    route_after_gaps,
    route_after_outline_approval,
    route_after_plan_approval,
)
from dr_core.research.source_records import merge_duplicate_records
from dr_core.research.text import format_reference, tokenize
from dr_core.providers.mocks import validate_mock_url


def _brief(**overrides) -> ResearchBrief:
    values = {
        "project_id": uuid4(),
        "run_id": uuid4(),
        "title": "Research",
        "topic": "AI regulation",
        "language": ProjectLanguage.EN,
        "research_depth": ResearchDepth.STANDARD,
    }
    values.update(overrides)
    return ResearchBrief(**values)


def test_plan_rejects_duplicate_questions_and_outline_requires_contiguous_indexes():
    with pytest.raises(ValidationError):
        ResearchPlan(
            summary="Plan",
            questions=[
                ResearchQuestion(question="Same question"),
                ResearchQuestion(question="same question"),
            ],
        )

    with pytest.raises(ValidationError):
        Outline(
            title="Outline",
            summary="Summary",
            sections=[
                OutlineSection(index=0, title="A", purpose="A"),
                OutlineSection(index=2, title="B", purpose="B"),
            ],
        )


def test_plan_generation_is_depth_aware_and_consumes_feedback():
    plan = build_plan(_brief())
    assert 3 <= len(plan.questions) <= 5
    assert plan.version == 1

    revised = build_plan(_brief(), version=2, feedback="Add enforcement costs")
    assert revised.version == 2
    assert any("enforcement costs" in item.question for item in revised.questions)

    quick = build_plan(_brief(research_depth=ResearchDepth.QUICK))
    assert len(quick.questions) == 3


def test_approval_schema_only_accepts_decision_values():
    assert (
        ApprovalDecision(decision=ApprovalStatus.APPROVED).decision
        is ApprovalStatus.APPROVED
    )
    with pytest.raises(ValidationError):
        ApprovalDecision(decision=ApprovalStatus.PENDING)


def test_routing_uses_feedback_and_gap_budget():
    assert (
        route_after_plan_approval(
            {"feedback": {"decision": "rejected"}}
        )
        == "plan_research"
    )
    assert route_after_plan_approval({"feedback": {}}) == "generate_queries"
    assert route_after_outline_approval(
        {"feedback": {"decision": "rejected"}}
    ) == "generate_outline"
    assert route_after_outline_approval({"feedback": {}}) == "write_sections"
    gap = ResearchGap(question="Open question", reason="Missing evidence")
    assert (
        route_after_gaps(
            {"gaps": [gap.model_dump(mode="json")], "budget_stopped": False}
        )
        == "revise_search_plan"
    )
    assert (
        route_after_gaps(
            {"gaps": [gap.model_dump(mode="json")], "budget_stopped": True}
        )
        == "generate_outline"
    )


def test_budget_values_use_the_strictest_positive_limit():
    settings = Settings(_env_file=None, research_cost_budget_usd=2.0)
    run = ResearchRun(
        cost_usd=Decimal("1.5"),
        input_tokens=10,
        output_tokens=5,
    )
    budget = _budget_values(run, settings, Decimal("1.0"))
    assert budget["cost_budget"] == Decimal("1.0")
    assert budget["cost_exhausted"] is True

    token_settings = Settings(_env_file=None, research_token_budget=20)
    token_budget = _budget_values(run, token_settings, None)
    assert token_budget["token_exhausted"] is False

    run.input_tokens = 20
    token_budget = _budget_values(run, token_settings, None)
    assert token_budget["token_exhausted"] is True


def test_source_records_keep_provider_score_and_rank_score_separate():
    record = SourceRecord(
        source_id=uuid4(),
        title="Source",
        provider="mock",
        score=0.9,
        rank_score=1.25,
    )
    assert record.score == 0.9
    assert record.rank_score == 1.25
    with pytest.raises(ValidationError):
        SourceRecord(
            source_id=uuid4(),
            title="Source",
            provider="mock",
            score=1.25,
        )


def test_preprint_versions_merge_into_one_source():
    first_question, second_question = uuid4(), uuid4()
    first = SourceRecord(
        source_id=uuid4(),
        title="Accessibility barriers",
        provider="crossref",
        doi="10.31234/osf.io/abcde_v1",
        url="https://osf.io/preprints/psyarxiv/abcde_v1",
        question_ids=[first_question],
    )
    second = SourceRecord(
        source_id=uuid4(),
        title="Accessibility barriers",
        provider="crossref",
        doi="10.31234/osf.io/abcde_v2",
        url="https://osf.io/preprints/psyarxiv/abcde_v2",
        question_ids=[second_question],
    )
    arxiv_v2 = SourceRecord(
        source_id=uuid4(),
        title="Accessible interfaces",
        provider="semantic_scholar",
        url="https://arxiv.org/abs/2101.00001v2",
    )
    arxiv_v5 = SourceRecord(
        source_id=uuid4(),
        title="Accessible interfaces",
        provider="semantic_scholar",
        url="https://arxiv.org/abs/2101.00001v5",
    )
    unrelated = SourceRecord(
        source_id=uuid4(),
        title="Accessibility law",
        provider="crossref",
        doi="10.1000/accessibility-law",
        url="https://example.com/accessibility-law",
    )

    merged = merge_duplicate_records(
        [first, second, arxiv_v2, arxiv_v5, unrelated]
    )

    assert len(merged) == 3
    assert merged[0].source_id == first.source_id
    assert merged[0].doi == "10.31234/osf.io/abcde_v1"
    assert merged[0].question_ids == [first_question, second_question]
    assert merged[1].source_id == arxiv_v2.source_id
    assert merged[2].source_id == unrelated.source_id


def test_reference_formatting_uses_only_supplied_metadata():
    reference = format_reference(
        authors=["Ada Lovelace", "Alan Turing"],
        title="Computing",
        year=2024,
        venue="Journal",
        publisher=None,
        doi="10.1000/example",
        url=None,
        style="apa",
    )
    assert "Ada Lovelace" in reference
    assert "2024" in reference
    assert "https://doi.org/10.1000/example" in reference
    assert "Unknown" not in reference


def test_tokenization_drops_stopwords_and_mock_urls_do_not_require_dns():
    tokens = tokenize("What are the core concepts and evidence quality?")
    assert "the" not in tokens
    assert "evidence" in tokens
    assert "quality" in tokens
    assert validate_mock_url("https://example.org/mock/1") == (
        "https://example.org/mock/1"
    )
