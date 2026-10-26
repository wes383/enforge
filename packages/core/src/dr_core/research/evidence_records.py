"""Build evidence rows from retrieved sentences."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from dr_core.db.enums import EvidenceType, EvidenceVerificationStatus
from dr_core.research.text import context_window, sentence_spans
from dr_core.schemas import EvidenceCreate

# Real research prose refutes a claim by reporting an absent effect, not by
# saying "however". A literal contrast-word list found no refutations on real
# corpora, so the patterns below also cover the negations a study uses when it
# finds nothing: "no significant difference", "did not replicate", "found no
# association". Limitation sentences such as "the design cannot establish
# causality" stay out on purpose; they describe the study, not its result.
_CONTRAST_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bhowever\b",
        r"\bbut\b",
        r"\bcontradict",
        r"\bcontrary\b",
        r"\brefut",
        r"\bdisagree",
        r"\bdispute",
        r"\bundermine",
        r"\binconsistent with\b",
        r"\bat odds with\b",
        "相反",
        "然而",
        "但是",
        "反驳",
        "质疑",
        "矛盾",
        "不一致",
    )
)
_NEGATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bno\s+(?:significant|measurable|reliable|consistent|detectable|"
        r"established|clear|evidence|association|effect|relationship|"
        r"difference|dose[- ]response)\b",
        r"\bno\s+(?:\w+\s+){0,2}stud(?:y|ies)\b",
        r"\bnot\s+(?:significantly\s+)?(?:associated|correlated|related|"
        r"support(?:ed|s)?|differ(?:s|ed)?|consistent|show(?:s|ed|n)?|"
        r"find|found|reach(?:ed)?|establish(?:ed)?|improve(?:s|d)?|"
        r"raise(?:s|d)?|lower(?:s|ed)?|cause(?:s|d)?|lead(?:s|ing)?)\b",
        r"\b(?:did|does|do)(?:n't| not)\s+(?:differ|support|show|agree|"
        r"find|replicate|detect|observe|increase|reduce|affect|predict|"
        r"reach|establish|constitute|improve|raise|lower|cause|lead)\b",
        r"\bfail(?:ed|s)?\s+to\s+(?:replicate|find|detect|show|confirm|"
        r"reach|improve)\b",
        r"\bunsupported\b",
        r"\bno\s+support(?:ing|ed)?\b",
        "不显著",
        "无显著",
        "没有显著",
        "无关联",
        "无关",
        "不支持",
        "未能",
        "没有证据",
        "未发现",
        "未观察到",
    )
)
_REFUTATION_PATTERNS = _CONTRAST_PATTERNS + _NEGATION_PATTERNS


def has_refutation_cue(sentence: str) -> bool:
    lowered = " ".join(sentence.casefold().split())
    return any(pattern.search(lowered) for pattern in _REFUTATION_PATTERNS)


def negates_finding(sentence: str) -> bool:
    """Whether the sentence reports a negative result, not just a contrast.

    A claim such as "enforcement does not improve compliance" states the
    negative finding itself; a refutation quote supports it. A claim that
    merely starts with "however" does not, so the contrast words are excluded.
    """
    lowered = " ".join(sentence.casefold().split())
    return any(pattern.search(lowered) for pattern in _NEGATION_PATTERNS)


def evidence_type(sentence: str) -> EvidenceType:
    if has_refutation_cue(sentence):
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
