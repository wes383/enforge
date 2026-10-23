"""Build evidence rows from retrieved sentences."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dr_core.db.enums import EvidenceType, EvidenceVerificationStatus
from dr_core.research.text import context_window, sentence_spans
from dr_core.schemas import EvidenceCreate


def evidence_type(sentence: str) -> EvidenceType:
    lowered = sentence.casefold()
    refutation_terms = (
        "however",
        "but ",
        "contradict",
        "contrary",
        "refute",
        "相反",
        "然而",
        "但是",
        "反驳",
    )
    if any(term in lowered for term in refutation_terms):
        return EvidenceType.REFUTATION
    if any(character.isdigit() for character in sentence):
        return EvidenceType.STATISTIC
    return EvidenceType.DIRECT_SUPPORT


def evidence_from_hit(
    hit: Any,
    *,
    section_id: UUID | None,
    sentence: str,
    start: int,
    end: int,
    match: str = "lexical",
    similarity: float | None = None,
) -> EvidenceCreate:
    raw = hit.text[start:end]
    verified = " ".join(raw.split()) == sentence
    before, after = context_window(hit.text, start, end)
    locator = {
        **hit.locator,
        "url": hit.url,
        "source_title": hit.source_title,
        "character_start": start,
        "character_end": end,
        "sentence_index": sentence_index(hit.text, start),
        "match": match,
        "retrieval": {
            "score": hit.score,
            "keyword_score": hit.keyword_score,
            "vector_score": hit.vector_score,
            "keyword_rank": hit.keyword_rank,
            "vector_rank": hit.vector_rank,
        },
    }
    if similarity is not None:
        locator["similarity"] = round(float(similarity), 6)
    return EvidenceCreate(
        source_id=hit.source_id,
        source_version_id=hit.source_version_id,
        outline_section_id=section_id,
        evidence_type=evidence_type(sentence),
        claim_summary=sentence[:1000],
        quoted_text=sentence,
        context_before=before or None,
        context_after=after or None,
        locator=locator,
        verification_status=(
            EvidenceVerificationStatus.VERIFIED
            if verified
            else EvidenceVerificationStatus.UNVERIFIED
        ),
    )


def evidence_from_text(
    record: Any,
    *,
    section_id: UUID | None,
    text: str,
    sentence: str,
    start: int,
    end: int,
    kind: str = "source_text",
    match: str = "lexical",
    similarity: float | None = None,
) -> EvidenceCreate:
    raw = text[start:end]
    verified = " ".join(raw.split()) == sentence
    before, after = context_window(text, start, end)
    locator = {
        "kind": kind,
        "source_version_id": str(record.source_version_id),
        "url": record.fetch_url or record.url,
        "provider": record.provider,
        "source_title": record.title,
        "character_start": start,
        "character_end": end,
        "sentence_index": sentence_index(text, start),
        "text_length": len(text),
        "match": match,
    }
    if similarity is not None:
        locator["similarity"] = round(float(similarity), 6)
    return EvidenceCreate(
        source_id=record.source_id,
        source_version_id=record.source_version_id,
        outline_section_id=section_id,
        evidence_type=evidence_type(sentence),
        claim_summary=sentence[:1000],
        quoted_text=sentence,
        context_before=before or None,
        context_after=after or None,
        locator=locator,
        verification_status=(
            EvidenceVerificationStatus.VERIFIED
            if verified
            else EvidenceVerificationStatus.UNVERIFIED
        ),
    )


def sentence_index(text: str, start: int) -> int | None:
    for index, (_, span_start, _) in enumerate(sentence_spans(text)):
        if span_start == start:
            return index
    return None
