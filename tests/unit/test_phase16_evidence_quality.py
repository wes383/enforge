from __future__ import annotations

from uuid import uuid4

import pytest

from dr_core.config import Settings
from dr_core.db.enums import EvidenceType, SectionRole, SourceVersionKind
from dr_core.db.models import SourceVersion
from dr_core.evaluation.metrics import _QuotedVersions
from dr_core.research.evidence_records import evidence_type
from dr_core.research.models import OutlineSection
from dr_core.research.nodes import _SectionEvidenceState, _section_quotas
from dr_core.research.quality import section_evidence_target


def _section(index: int = 0, *, target_words: int = 400) -> OutlineSection:
    return OutlineSection(
        index=index,
        role=SectionRole.BODY,
        title=f"Section {index}",
        purpose="Explain the evidence.",
        target_words=target_words,
    )


def _states(
    sections: list[OutlineSection],
    *,
    target_evidence: int = 10,
    target_identities: int = 4,
) -> dict:
    return {
        section.section_id: _SectionEvidenceState(
            target_evidence=target_evidence,
            target_identities=target_identities,
        )
        for section in sections
    }


@pytest.mark.parametrize(
    "sentence",
    [
        "The effect does not differ significantly from zero.",
        "The pooled evidence does not support the large gains reported by surveys.",
        "Six found no association after adjustment.",
        "Two of those four failed to replicate.",
        "No study demonstrated a consistent adverse effect at these concentrations.",
        "Supervisor ratings were not associated with measured output.",
        "These studies do not agree, and the gap is systematic.",
        "在调整后未发现显著关联。",
    ],
)
def test_a_negative_result_is_refutation_evidence(sentence):
    assert evidence_type(sentence) is EvidenceType.REFUTATION


@pytest.mark.parametrize(
    "sentence",
    [
        "The design cannot establish causality.",
        "We did not measure health outcomes.",
        "The gain persisted after controlling for role and tenure.",
        "C-reactive protein rose by 0.12 milligrams per litre.",
    ],
)
def test_limitations_and_positive_results_are_not_refutations(sentence):
    assert evidence_type(sentence) is not EvidenceType.REFUTATION


def test_quotas_split_the_budget_evenly_when_every_section_needs_the_same():
    sections = [_section(index) for index in range(3)]
    quotas = _section_quotas(sections, _states(sections), 30)

    assert sum(quotas.values()) == 30
    assert set(quotas.values()) == {10}


def test_quotas_follow_each_sections_unmet_target():
    sections = [_section(0), _section(1)]
    states = _states(sections)
    states[sections[0].section_id].support = 8

    quotas = _section_quotas(sections, states, 12)

    assert quotas[sections[0].section_id] == 2
    assert quotas[sections[1].section_id] == 10
    assert sum(quotas.values()) == 12


def test_a_reached_section_reserves_no_budget():
    sections = [_section(0), _section(1)]
    states = _states(sections)
    states[sections[0].section_id].support = 10
    states[sections[0].section_id].identities = {"a", "b", "c", "d"}

    quotas = _section_quotas(sections, states, 7)

    assert sections[0].section_id not in quotas
    assert quotas[sections[1].section_id] == 7


def test_quotas_still_allocate_every_item_when_budget_is_below_section_count():
    sections = [_section(index) for index in range(3)]
    quotas = _section_quotas(sections, _states(sections), 2)

    assert sum(quotas.values()) == 2
    assert len([value for value in quotas.values() if value]) == 2


def test_quotas_are_empty_without_pending_sections_or_budget():
    sections = [_section(0)]
    states = _states(sections)
    states[sections[0].section_id].support = 10
    states[sections[0].section_id].identities = {"a", "b", "c", "d"}

    assert _section_quotas(sections, states, 5) == {}

    pending_sections = [_section(1)]
    assert (
        _section_quotas(pending_sections, _states(pending_sections), 0) == {}
    )


def test_the_source_target_is_capped_by_the_sources_the_run_collected():
    settings = Settings(_env_file=None)
    section = _section(target_words=750)

    _, unbounded = section_evidence_target(section, settings)
    _, capped = section_evidence_target(
        section,
        settings,
        available_sources=5,
    )

    assert unbounded == 10
    assert capped == 5


class _MemoryStorage:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def read(self, key: str) -> bytes:
        return self._files[key].encode("utf-8")


def _version(
    source_id,
    *,
    number: int,
    kind: SourceVersionKind,
    storage_key: str | None = None,
    snippet: str | None = None,
) -> SourceVersion:
    return SourceVersion(
        id=uuid4(),
        project_id=uuid4(),
        source_id=source_id,
        version_number=number,
        content_kind=kind,
        content_hash=f"{number:064d}",
        storage_key=storage_key,
        version_metadata={"snippet": snippet} if snippet else {},
    )


def test_a_quote_is_traceable_through_another_version_of_the_same_source():
    source_id = uuid4()
    snippet = _version(
        source_id,
        number=1,
        kind=SourceVersionKind.SEARCH_SNIPPET,
        snippet="A snippet carrying the quoted sentence.",
    )
    full_text = _version(
        source_id,
        number=2,
        kind=SourceVersionKind.FULL_TEXT,
        storage_key="sources/full.md",
    )
    quoted = _QuotedVersions(
        _MemoryStorage({"sources/full.md": "Full text without that sentence."}),
        {source_id: [snippet, full_text]},
    )

    assert quoted.contains(source_id, "the quoted sentence") is True
    assert quoted.contains(source_id, "a sentence never written") is False
    assert quoted.contains(uuid4(), "the quoted sentence") is False
