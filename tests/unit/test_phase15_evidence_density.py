from __future__ import annotations

from uuid import uuid4

from dr_core.config import Settings
from dr_core.db.enums import SectionRole
from dr_core.research.models import OutlineSection
from dr_core.research.nodes import _SectionEvidenceState
from dr_core.research.quality import section_evidence_target


def _settings(**overrides) -> Settings:
    values = {
        "research_section_target_words_per_evidence": 35,
        "research_section_target_evidence_min": 6,
        "research_section_target_evidence_max": 30,
        "research_section_target_sources_min": 4,
        "research_section_target_sources_max": 10,
    }
    values.update(overrides)
    return Settings(**values)


def _section(target_words: int) -> OutlineSection:
    return OutlineSection(
        index=0,
        role=SectionRole.BODY,
        title="片尾字幕的历史",
        purpose="解释名单如何变长。",
        search_terms=["end credits history"],
        target_words=target_words,
    )


def test_a_source_may_support_a_section_several_times():
    state = _SectionEvidenceState(per_source_limit=3)
    source_id = uuid4()

    assert state.can_quote(source_id) is True
    for _ in range(3):
        state.track(source_id, "doi:10.1234/study", is_refutation=False)

    assert state.can_quote(source_id) is False
    assert state.support == 3
    # Independence is counted by identity, so three quotes from one study are
    # still one independent source.
    assert len(state.identities) == 1


def test_the_per_source_limit_is_configurable():
    state = _SectionEvidenceState(per_source_limit=1)
    source_id = uuid4()

    state.track(source_id, "doi:10.1234/study", is_refutation=False)

    assert state.can_quote(source_id) is False


def test_a_repeated_sentence_is_not_quoted_twice():
    state = _SectionEvidenceState()

    state.remember("End credits grew steadily.")

    assert state.already_quoted("  end   credits   grew steadily. ") is True
    assert state.already_quoted("Another sentence entirely.") is False


def test_a_refutation_counts_towards_the_source_limit_but_not_support():
    state = _SectionEvidenceState(per_source_limit=2)
    source_id = uuid4()

    state.track(source_id, "doi:10.1234/study", is_refutation=True)

    assert state.support == 0
    assert state.identities == set()
    assert state.can_quote(source_id) is True
    state.track(source_id, "doi:10.1234/study", is_refutation=False)
    assert state.can_quote(source_id) is False


def test_a_long_section_is_asked_for_more_evidence_than_a_short_one():
    long_evidence, long_sources = section_evidence_target(_section(750), _settings())
    short_evidence, short_sources = section_evidence_target(_section(150), _settings())

    assert long_evidence == 21  # 750 words at one fact per 35 words
    assert short_evidence == 6  # clamped up to the minimum
    assert long_evidence > short_evidence
    assert long_sources == 10
    assert short_sources == 4


def test_the_evidence_target_is_clamped_to_the_configured_ceiling():
    evidence, _ = section_evidence_target(
        _section(3000),
        _settings(research_section_target_evidence_max=12),
    )

    assert evidence == 12
