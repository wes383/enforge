from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dr_core.config.settings import Settings
from dr_core.db.enums import (
    EvidenceType,
    ProjectLanguage,
    ResearchDepth,
    SectionCoverageStatus,
    SectionRole,
    SourceType,
)
from dr_core.providers.errors import ProviderResponseError
from dr_core.providers.llm import _parse_completion
from dr_core.research.models import (
    EvidenceMatrix,
    EvidenceRow,
    OutlineSection,
    ResearchBrief,
    ResearchGap,
)
from dr_core.research.nodes import _section_gaps
from dr_core.research.quality import (
    build_evidence_matrix,
    section_evidence_target,
)
from dr_core.research.writing import _default_token_budget, _validate_llm_markdown


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


def _section(*, target_words=400, title="Enforcement outcomes") -> OutlineSection:
    return OutlineSection(
        index=1,
        title=title,
        role=SectionRole.BODY,
        purpose="Synthesize the evidence.",
        target_words=target_words,
    )


def _source(*, title="Study", doi=None):
    return SimpleNamespace(
        id=uuid4(),
        title=title,
        authors=["Ada Lovelace"],
        published_at=date(2024, 5, 1),
        venue="Journal of Tests",
        publisher="Example Press",
        doi=doi,
        canonical_url="https://example.org/study",
        source_type=SourceType.JOURNAL_ARTICLE,
    )


def _evidence(section_id, source_id, *, evidence_type=EvidenceType.DIRECT_SUPPORT):
    return SimpleNamespace(
        id=uuid4(),
        outline_section_id=section_id,
        source_id=source_id,
        source_version_id=uuid4(),
        evidence_type=evidence_type,
        claim_summary="Enforcement improves compliance in audited firms.",
        quoted_text="Enforcement improves compliance among audited firms.",
        context_before="The study reports the following result.",
        context_after="The authors discuss limitations next.",
        locator={"kind": "source_text"},
    )


def test_section_evidence_target_scales_with_the_word_budget():
    settings = Settings(_env_file=None)

    short_evidence, short_sources = section_evidence_target(
        _section(target_words=200), settings
    )
    long_evidence, long_sources = section_evidence_target(
        _section(target_words=1200), settings
    )

    assert short_evidence == settings.research_section_target_evidence_min
    assert long_evidence == settings.research_section_target_evidence_max
    assert short_sources >= settings.research_section_target_sources_min
    assert long_sources >= short_sources


def test_section_evidence_target_respects_configured_bounds():
    settings = Settings(
        _env_file=None,
        research_section_target_words_per_evidence=40,
        research_section_target_evidence_min=2,
        research_section_target_evidence_max=5,
        research_section_target_sources_min=2,
        research_section_target_sources_max=4,
    )

    evidence, sources = section_evidence_target(
        _section(target_words=1000), settings
    )

    assert evidence == 5
    assert sources == 3


def test_evidence_matrix_reports_the_achievement_ratio():
    section = _section()
    source_a = _source(title="First study")
    source_b = _source(title="Second study")
    support_a = _evidence(section.section_id, source_a.id)
    support_b = _evidence(section.section_id, source_b.id)

    matrix = build_evidence_matrix(
        [section],
        [support_a, support_b],
        {source_a.id: source_a, source_b.id: source_b},
        language=ProjectLanguage.EN,
        now_year=2026,
        targets={section.section_id: (8, 4)},
    )

    row = matrix.rows[0]
    assert row.coverage_status is SectionCoverageStatus.COVERED
    assert row.target_evidence == 8
    assert row.target_sources == 4
    assert row.evidence_ratio == pytest.approx(0.25)
    assert matrix.target_ratio == pytest.approx(0.25)


def test_evidence_ratio_falls_back_to_coverage_without_a_target():
    section = _section()
    source_a = _source(title="First study")
    source_b = _source(title="Second study")
    support_a = _evidence(section.section_id, source_a.id)
    support_b = _evidence(section.section_id, source_b.id)

    matrix = build_evidence_matrix(
        [section],
        [support_a, support_b],
        {source_a.id: source_a, source_b.id: source_b},
        language=ProjectLanguage.EN,
        now_year=2026,
    )

    row = matrix.rows[0]
    assert row.target_evidence == 0
    assert row.evidence_ratio == 1.0
    assert matrix.target_ratio == 0.0


def _row(
    section_id,
    *,
    coverage=SectionCoverageStatus.COVERED,
    ratio=1.0,
    target=8,
) -> EvidenceRow:
    return EvidenceRow(
        section_id=section_id,
        section_title="Enforcement outcomes",
        support_count=2,
        independent_source_count=2,
        coverage_status=coverage,
        target_evidence=target,
        target_sources=4,
        evidence_ratio=ratio,
    )


def test_covered_section_below_its_target_raises_a_gap():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[_row(section_id, ratio=0.25)],
        total_evidence=2,
        source_count=2,
        independent_source_count=2,
    )

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.BODY},
        terms_by_section={section_id: ["enforcement", "audits"]},
        previous_gaps=[],
        round_index=1,
    )

    under_target = [gap for gap in gaps if gap.section_id == section_id]
    assert under_target
    assert under_target[0].severity == "low"
    assert under_target[0].target_evidence == 8
    assert under_target[0].achieved_ratio == 0.25
    assert under_target[0].suggested_queries


def test_covered_section_at_its_target_raises_no_gap():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[_row(section_id, ratio=1.0)],
        total_evidence=8,
        source_count=4,
        independent_source_count=4,
    )

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.BODY},
        terms_by_section={section_id: ["enforcement"]},
        previous_gaps=[],
        round_index=2,
    )

    assert [gap for gap in gaps if gap.section_id == section_id] == []


def test_under_target_gap_closes_when_a_round_does_not_improve():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[_row(section_id, ratio=0.25)],
        total_evidence=2,
        source_count=2,
        independent_source_count=2,
    )
    previous = [
        ResearchGap(
            section_id=section_id,
            section_title="Enforcement outcomes",
            reason="Still below the target.",
            severity="low",
            target_evidence=8,
            achieved_ratio=0.25,
        )
    ]

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.BODY},
        terms_by_section={section_id: ["enforcement"]},
        previous_gaps=previous,
        round_index=2,
    )

    assert [gap for gap in gaps if gap.section_id == section_id] == []


def test_under_target_gap_stays_open_when_the_ratio_improves():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[_row(section_id, ratio=0.5)],
        total_evidence=4,
        source_count=3,
        independent_source_count=3,
    )
    previous = [
        ResearchGap(
            section_id=section_id,
            section_title="Enforcement outcomes",
            reason="Still below the target.",
            severity="low",
            target_evidence=8,
            achieved_ratio=0.25,
        )
    ]

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.BODY},
        terms_by_section={section_id: ["enforcement"]},
        previous_gaps=previous,
        round_index=2,
    )

    assert [gap for gap in gaps if gap.section_id == section_id]


def test_incomplete_section_keeps_its_severe_gap():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[
            _row(
                section_id,
                coverage=SectionCoverageStatus.INSUFFICIENT,
                ratio=0.0,
                target=0,
            )
        ],
        total_evidence=0,
    )

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.BODY},
        terms_by_section={section_id: ["enforcement"]},
        previous_gaps=[],
        round_index=1,
    )

    assert [gap.severity for gap in gaps if gap.section_id == section_id] == [
        "high"
    ]


def test_limitations_section_never_raises_a_gap():
    section_id = uuid4()
    matrix = EvidenceMatrix(
        rows=[
            _row(
                section_id,
                coverage=SectionCoverageStatus.INSUFFICIENT,
                ratio=0.0,
                target=0,
            )
        ],
        total_evidence=0,
    )

    gaps = _section_gaps(
        matrix,
        brief=_brief(),
        roles={section_id: SectionRole.LIMITATIONS},
        terms_by_section={section_id: []},
        previous_gaps=[],
        round_index=1,
    )

    assert gaps == []


def test_empty_answer_from_a_reasoning_model_is_reported_as_truncation():
    payload = {
        "model": "reasoner",
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "thinking hard"},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 900},
    }

    with pytest.raises(ProviderResponseError) as error:
        _parse_completion(payload, "reasoner")

    assert "reasoning" in str(error.value)
    assert "length" in str(error.value)


def test_missing_message_object_is_distinguished_from_an_empty_answer():
    payload = {
        "model": "gpt",
        "choices": [{"finish_reason": "stop", "message": None}],
    }

    with pytest.raises(ProviderResponseError) as error:
        _parse_completion(payload, "gpt")

    assert "message object" in str(error.value)


def test_completion_reports_reasoning_tokens():
    payload = {
        "model": "reasoner",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "answer", "reasoning_content": "thinking"},
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 900,
            "completion_tokens_details": {"reasoning_tokens": 640},
        },
    }

    response = _parse_completion(payload, "reasoner")

    assert response.finish_reason == "stop"
    assert response.reasoning_tokens == 640
    assert response.reasoning_text == "thinking"


def test_llm_markdown_repair_keeps_the_citable_paragraph():
    section = _section()
    source = _source()
    evidence = _evidence(section.section_id, source.id)
    allowed = {"S1": source.id}
    markdown = (
        "## Section title the model invented\n\n"
        "A paragraph without any citation at all.\n\n"
        "Enforcement improves compliance among audited firms [S1].\n\n"
        "Another paragraph without a citation.\n"
    )

    content, reason = _validate_llm_markdown(
        markdown,
        section=section,
        allowed_markers=allowed,
        evidence=[evidence],
        sources={source.id: source},
    )

    assert reason == ""
    assert content is not None
    assert content.startswith("## Enforcement outcomes")
    assert "[S1]" in content
    assert "paragraph without any citation" not in content


def test_llm_markdown_is_rejected_when_nothing_survives():
    section = _section()
    source = _source()
    allowed = {"S1": source.id}

    content, reason = _validate_llm_markdown(
        "A paragraph without any citation at all.",
        section=section,
        allowed_markers=allowed,
        evidence=[],
        sources={source.id: source},
    )

    assert content is None
    assert reason


def test_write_token_budget_keeps_a_floor_for_short_sections():
    section = _section(target_words=100)

    assert _default_token_budget(section) >= 1200
    assert _default_token_budget(_section(target_words=1200)) == 7200
