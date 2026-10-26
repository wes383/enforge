from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping
from uuid import UUID

from dr_core.config.settings import Settings
from dr_core.db.enums import (
    EvidenceType,
    ProjectLanguage,
    SectionCoverageStatus,
    SourceType,
)
from dr_core.research.models import (
    EvidenceMatrix,
    EvidenceQuality,
    EvidenceRow,
    QualityReason,
    OutlineSection,
    SourceQuality,
)

_PRIMARY_TYPES = frozenset(
    {
        SourceType.JOURNAL_ARTICLE,
        SourceType.CONFERENCE_PAPER,
        SourceType.PREPRINT,
        SourceType.BOOK,
        SourceType.REPORT,
        SourceType.DATASET,
        SourceType.USER_DOCUMENT,
    }
)
_CONDITIONAL_MARKERS = (
    " among ",
    " for ",
    " if ",
    " in ",
    " under ",
    " when ",
    " within ",
    " unless ",
    "条件下",
    "情况下",
    "对于",
    "当中",
    "如果",
    "仅在",
)
_METHOD_KEYS = (
    "method",
    "methods",
    "methodology",
    "sample_size",
    "data_availability",
    "open_access",
)
_TITLE_NOISE = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+")


def normalize_title_key(title: str) -> str:
    return _TITLE_NOISE.sub("", title.casefold())


# The key is stored on SourceQuality and compared for duplicate detection. A
# scraped page title can be arbitrarily long, so the key is bounded here rather
# than letting a single long title fail the whole evidence analysis.
_IDENTITY_MAX_CHARS = 400


def source_identity(source: Any) -> str:
    doi = getattr(source, "doi", None)
    if doi:
        return f"doi:{doi.casefold()}"[:_IDENTITY_MAX_CHARS]
    title = getattr(source, "title", "") or ""
    year = _published_year(source)
    title_key = normalize_title_key(title)
    if title_key:
        key = f"title:{title_key}:{year or 'unknown'}"
        return key[:_IDENTITY_MAX_CHARS]
    url = getattr(source, "canonical_url", None)
    if url:
        return f"url:{url.casefold()}"[:_IDENTITY_MAX_CHARS]
    source_id = getattr(source, "id", None)
    return f"source:{source_id}"


def independent_source_keys(sources: Iterable[Any]) -> set[str]:
    return {source_identity(source) for source in sources}


def assess_source_quality(
    source: Any,
    *,
    now_year: int | None = None,
) -> SourceQuality:
    now_year = now_year or datetime.now(tz=UTC).year
    source_type = getattr(source, "source_type", SourceType.OTHER)
    metadata = dict(getattr(source, "source_metadata", None) or {})
    authors = list(getattr(source, "authors", None) or [])
    publisher = getattr(source, "publisher", None)
    venue = getattr(source, "venue", None)
    doi = getattr(source, "doi", None)
    is_primary = source_type in _PRIMARY_TYPES
    reasons: list[QualityReason] = []

    if is_primary:
        reasons.append(
            QualityReason(
                dimension="primary_source",
                status="positive",
                reason=_source_type_reason(source_type, primary=True),
            )
        )
    elif source_type is SourceType.WEB_PAGE:
        reasons.append(
            QualityReason(
                dimension="primary_source",
                status="neutral",
                reason=(
                    "The item is a web page; it may be secondary reporting rather "
                    "than the original study."
                ),
            )
        )
    else:
        reasons.append(
            QualityReason(
                dimension="primary_source",
                status="unknown",
                reason="The source type does not establish whether this is primary material.",
            )
        )

    if authors or publisher or venue or doi:
        attribution = ", ".join(
            part
            for part in (
                f"authors: {len(authors)}" if authors else "",
                f"publisher: {publisher}" if publisher else "",
                f"venue: {venue}" if venue else "",
                f"doi: {doi}" if doi else "",
            )
            if part
        )
        reasons.append(
            QualityReason(
                dimension="attribution",
                status="positive",
                reason=f"Bibliographic attribution is available ({attribution}).",
            )
        )
        attribution_status = "positive"
    else:
        reasons.append(
            QualityReason(
                dimension="attribution",
                status="concern",
                reason="No author, publisher, venue, or DOI metadata was available.",
            )
        )
        attribution_status = "concern"

    year = _published_year(source)
    if year is None:
        reasons.append(
            QualityReason(
                dimension="recency",
                status="unknown",
                reason="Publication date is missing; temporal relevance cannot be judged.",
            )
        )
        recency_status = "unknown"
    else:
        age = now_year - year
        if age <= 5:
            recency_status = "positive"
            reasons.append(
                QualityReason(
                    dimension="recency",
                    status="positive",
                    reason=f"Published in {year}, within five years of the current year.",
                )
            )
        elif age <= 10:
            recency_status = "neutral"
            reasons.append(
                QualityReason(
                    dimension="recency",
                    status="neutral",
                    reason=f"Published in {year}; relevance depends on the section.",
                )
            )
        else:
            recency_status = "concern"
            reasons.append(
                QualityReason(
                    dimension="recency",
                    status="concern",
                    reason=f"Published in {year}; the evidence may be dated.",
                )
            )

    if any(key in metadata for key in _METHOD_KEYS):
        reasons.append(
            QualityReason(
                dimension="method_transparency",
                status="positive",
                reason="The provider metadata includes method, data, or access details.",
            )
        )
        transparency_status = "positive"
    elif is_primary:
        reasons.append(
            QualityReason(
                dimension="method_transparency",
                status="unknown",
                reason="The retrieved metadata does not describe methods or materials.",
            )
        )
        transparency_status = "unknown"
    else:
        reasons.append(
            QualityReason(
                dimension="method_transparency",
                status="unknown",
                reason="No method or material information is available for this web source.",
            )
        )
        transparency_status = "unknown"

    if metadata.get("retracted") is True:
        reasons.append(
            QualityReason(
                dimension="corrections",
                status="concern",
                reason="The metadata marks this work as retracted.",
            )
        )
        correction_status = "concern"
    elif metadata.get("correction") is True:
        reasons.append(
            QualityReason(
                dimension="corrections",
                status="concern",
                reason="The metadata indicates a published correction.",
            )
        )
        correction_status = "concern"
    else:
        reasons.append(
            QualityReason(
                dimension="corrections",
                status="unknown",
                reason=(
                    "No retraction or correction information was available; "
                    "absence of a flag is not proof that none exists."
                ),
            )
        )
        correction_status = "unknown"

    concerns = sum(1 for reason in reasons if reason.status == "concern")
    positives = sum(1 for reason in reasons if reason.status == "positive")
    if correction_status == "concern" or (
        attribution_status == "concern" and not is_primary
    ):
        level = "low"
    elif (
        is_primary
        and positives >= 3
        and transparency_status == "positive"
        and concerns == 0
    ):
        level = "high"
    elif positives >= 2:
        level = "moderate"
    elif concerns == 0:
        level = "unknown"
    else:
        level = "low"

    return SourceQuality(
        source_id=getattr(source, "id"),
        identity_key=source_identity(source),
        level=level,
        is_primary=is_primary,
        reasons=reasons,
    )


def assess_evidence_quality(
    evidence: Any,
    *,
    source: Any | None,
    counter_evidence_present: bool,
    identity_shared: bool,
) -> EvidenceQuality:
    evidence_type = getattr(evidence, "evidence_type", EvidenceType.QUALITATIVE)
    direct = evidence_type in {
        EvidenceType.DIRECT_SUPPORT,
        EvidenceType.REFUTATION,
        EvidenceType.STATISTIC,
    }
    has_context = bool(
        getattr(evidence, "context_before", None)
        or getattr(evidence, "context_after", None)
    )
    independence = "shared" if identity_shared else "independent"
    quote = (getattr(evidence, "quoted_text", "") or "").casefold()
    conditional = any(marker in f" {quote} " for marker in _CONDITIONAL_MARKERS)
    reasons: list[QualityReason] = []

    if direct:
        reasons.append(
            QualityReason(
                dimension="directness",
                status="positive",
                reason=(
                    "The quoted passage directly supports or refutes the section "
                    f"as {evidence_type.value} evidence."
                ),
            )
        )
        directness = "direct"
    else:
        reasons.append(
            QualityReason(
                dimension="directness",
                status="neutral",
                reason=(
                    f"The passage is {evidence_type.value} evidence and requires "
                    "an inferential step to address the section."
                ),
            )
        )
        directness = "indirect"

    if str((getattr(evidence, "locator", None) or {}).get("match") or "") == "semantic":
        reasons.append(
            QualityReason(
                dimension="directness",
                status="concern",
                reason=(
                    "The quotation was selected by embedding similarity because the "
                    "query and the source share no term; read it before relying on it."
                ),
            )
        )

    if has_context:
        reasons.append(
            QualityReason(
                dimension="context_consistency",
                status="positive",
                reason="Surrounding text was preserved with the quotation.",
            )
        )
        context_consistency = "preserved"
    else:
        reasons.append(
            QualityReason(
                dimension="context_consistency",
                status="concern",
                reason="Only the quotation is stored; surrounding context is unavailable.",
            )
        )
        context_consistency = "quote_only"

    if identity_shared:
        reasons.append(
            QualityReason(
                dimension="independence",
                status="concern",
                reason=(
                    "Another source in this section appears to report the same "
                    "original work, so the items are not treated as independent."
                ),
            )
        )
    else:
        reasons.append(
            QualityReason(
                dimension="independence",
                status="positive",
                reason="No duplicate source identity was found for this evidence item.",
            )
        )

    if counter_evidence_present:
        reasons.append(
            QualityReason(
                dimension="counter_evidence",
                status="positive",
                reason="Contrary evidence was retained and considered for this section.",
            )
        )
        counter = "present"
    else:
        reasons.append(
            QualityReason(
                dimension="counter_evidence",
                status="neutral",
                reason="No contrary evidence was found; this does not prove it is absent.",
            )
        )
        counter = "none_found"

    if conditional:
        reasons.append(
            QualityReason(
                dimension="applicability",
                status="neutral",
                reason="The quotation contains conditions or a limited scope.",
            )
        )
        applicability = "conditional"
    else:
        reasons.append(
            QualityReason(
                dimension="applicability",
                status="unknown",
                reason="The quotation does not state its conditions of applicability.",
            )
        )
        applicability = "not_explicit"

    if direct and has_context and not identity_shared:
        level = "high"
    elif direct and (has_context or not identity_shared):
        level = "moderate"
    elif has_context or not identity_shared:
        level = "low"
    else:
        level = "uncertain"

    return EvidenceQuality(
        evidence_id=getattr(evidence, "id"),
        level=level,
        directness=directness,
        context_consistency=context_consistency,
        independence=independence,
        counter_evidence=counter,
        applicability=applicability,
        reasons=reasons,
    )


def section_evidence_target(
    section: OutlineSection,
    settings: Settings,
    *,
    available_sources: int | None = None,
) -> tuple[int, int]:
    """Evidence a section needs, derived from its own writing budget.

    The target scales with the section's word budget, so a short section
    stops earlier than a long one instead of every section sharing one
    hard-coded floor. ``available_sources`` caps the independent-source
    target at what the run actually collected; a target of eight sources is
    unreachable when the corpus holds five, and an unreachable target makes
    the achievement ratio meaningless.
    """
    words = section.target_words or 0
    wanted = words // max(1, settings.research_section_target_words_per_evidence)
    evidence = min(
        settings.research_section_target_evidence_max,
        max(settings.research_section_target_evidence_min, wanted),
    )
    identities = min(
        settings.research_section_target_sources_max,
        max(settings.research_section_target_sources_min, evidence // 2 + 1),
    )
    if available_sources is not None:
        identities = min(identities, max(0, available_sources))
    return evidence, identities


def _achievement_ratio(
    *,
    support_count: int,
    identity_count: int,
    target_evidence: int,
    target_sources: int,
    coverage: SectionCoverageStatus,
) -> float:
    """Share of a section's evidence target that was reached.

    Without a target the ratio falls back to the coverage status so the
    field stays meaningful for callers that do not know the target.
    """
    if target_evidence <= 0:
        if coverage is SectionCoverageStatus.COVERED:
            return 1.0
        if coverage is SectionCoverageStatus.PARTIAL:
            return 0.5
        return 0.0
    evidence_share = min(1.0, support_count / target_evidence)
    source_share = (
        min(1.0, identity_count / target_sources)
        if target_sources > 0
        else 1.0
    )
    return round(min(evidence_share, source_share), 4)


def build_evidence_matrix(
    sections: list[OutlineSection],
    evidence: list[Any],
    sources_by_id: dict[UUID, Any],
    *,
    language: ProjectLanguage,
    now_year: int | None = None,
    targets: Mapping[UUID, tuple[int, int]] | None = None,
) -> EvidenceMatrix:
    """Build the evidence matrix.

    ``targets`` maps a section id to the ``(evidence, independent sources)``
    the section needs to be considered well supported. Coverage status keeps
    its own floor of two supporting items from two independent sources; the
    target only drives the achievement ratio reported per row.
    """
    now_year = now_year or datetime.now(tz=UTC).year
    by_section: dict[UUID | None, list[Any]] = defaultdict(list)
    for item in evidence:
        by_section[getattr(item, "outline_section_id", None)].append(item)

    rows: list[EvidenceRow] = []
    all_identities: set[str] = set()
    identity_sources: dict[str, set[UUID]] = defaultdict(set)
    for item in evidence:
        source = sources_by_id.get(getattr(item, "source_id"))
        identity = source_identity(source) if source is not None else (
            f"source:{getattr(item, 'source_id')}"
        )
        all_identities.add(identity)
        identity_sources[identity].add(getattr(item, "source_id"))

    duplicate_groups = {
        identity: source_ids
        for identity, source_ids in identity_sources.items()
        if len(source_ids) > 1
    }
    duplicate_labels = [
        f"{identity} ({len(source_ids)} sources)"
        for identity, source_ids in sorted(duplicate_groups.items())
    ]

    for section in sections:
        items = by_section.get(section.section_id, [])
        support = [
            item for item in items if item.evidence_type is not EvidenceType.REFUTATION
        ]
        refute = [
            item for item in items if item.evidence_type is EvidenceType.REFUTATION
        ]
        used_sources = {
            item.source_id: sources_by_id.get(item.source_id)
            for item in items
            if item.source_id in sources_by_id
        }
        used_identities = {
            source_identity(source) if source is not None else f"source:{source_id}"
            for source_id, source in used_sources.items()
        }
        support_identities = {
            source_identity(sources_by_id.get(item.source_id))
            if sources_by_id.get(item.source_id) is not None
            else f"source:{item.source_id}"
            for item in support
        }
        refute_identities = {
            source_identity(sources_by_id.get(item.source_id))
            if sources_by_id.get(item.source_id) is not None
            else f"source:{item.source_id}"
            for item in refute
        }
        identity_counts: dict[str, int] = defaultdict(int)
        for item in items:
            source = sources_by_id.get(item.source_id)
            identity = (
                source_identity(source)
                if source is not None
                else f"source:{item.source_id}"
            )
            identity_counts[identity] += 1

        if len(support) >= 2 and len(support_identities) >= 2:
            coverage = SectionCoverageStatus.COVERED
        elif support:
            coverage = SectionCoverageStatus.PARTIAL
        else:
            coverage = SectionCoverageStatus.INSUFFICIENT

        row_limitations: list[str] = []
        if len(support_identities) == 0:
            row_limitations.append(
                "无独立来源支持该章节。"
                if language is ProjectLanguage.ZH
                else "No independent source supports this section."
            )
        elif len(support_identities) == 1:
            row_limitations.append(
                "仅有一个独立来源支持该章节，结论应视为初步判断。"
                if language is ProjectLanguage.ZH
                else "Only one independent source supports this section; treat the conclusion as provisional."
            )
        if len(used_identities) < len(used_sources):
            row_limitations.append(
                "部分来源可能转述同一原始研究，未按独立来源计数。"
                if language is ProjectLanguage.ZH
                else "Some sources appear to report the same original work and were not counted as independent."
            )
        if used_sources and all(
            not assess_source_quality(source, now_year=now_year).is_primary
            for source in used_sources.values()
            if source is not None
        ):
            row_limitations.append(
                "现有来源以网页或二手材料为主，原始研究信息有限。"
                if language is ProjectLanguage.ZH
                else "Available sources are mainly web or secondary material; primary-study detail is limited."
            )
        if support and not refute:
            row_limitations.append(
                "未发现相反证据，但这不等于不存在反例。"
                if language is ProjectLanguage.ZH
                else "No contrary evidence was found; this does not prove that none exists."
            )
        if any(_published_year(source) is None for source in used_sources.values() if source):
            row_limitations.append(
                "部分来源缺少出版日期，时间相关性无法判断。"
                if language is ProjectLanguage.ZH
                else "Some sources lack a publication date, so temporal relevance cannot be judged."
            )

        conclusion = _conclusion(
            support_count=len(support),
            refute_count=len(refute),
            support_identities=len(support_identities),
            refute_identities=len(refute_identities),
            coverage=coverage,
            language=language,
        )
        source_quality = [
            assess_source_quality(source, now_year=now_year)
            for source in sorted(
                (source for source in used_sources.values() if source is not None),
                key=lambda item: str(item.id),
            )
        ][:8]
        evidence_quality = [
            assess_evidence_quality(
                item,
                source=sources_by_id.get(item.source_id),
                counter_evidence_present=bool(refute),
                identity_shared=identity_counts[
                    source_identity(sources_by_id.get(item.source_id))
                    if sources_by_id.get(item.source_id) is not None
                    else f"source:{item.source_id}"
                ]
                > 1,
            )
            for item in items[:12]
        ]
        target_evidence, target_sources = (
            (targets or {}).get(section.section_id, (0, 0))
        )
        evidence_ratio = _achievement_ratio(
            support_count=len(support),
            identity_count=len(support_identities),
            target_evidence=target_evidence,
            target_sources=target_sources,
            coverage=coverage,
        )
        rows.append(
            EvidenceRow(
                section_id=section.section_id,
                section_title=section.title,
                support_count=len(support),
                refute_count=len(refute),
                source_count=len(used_sources),
                independent_source_count=len(used_identities),
                coverage_status=coverage,
                target_evidence=target_evidence,
                target_sources=target_sources,
                evidence_ratio=evidence_ratio,
                conclusion=conclusion,
                support_evidence_ids=sorted(
                    {item.id for item in support}, key=str
                ),
                refute_evidence_ids=sorted(
                    {item.id for item in refute}, key=str
                ),
                source_quality=source_quality,
                evidence_quality=evidence_quality,
                limitations=row_limitations,
                gap=(
                    None
                    if coverage is SectionCoverageStatus.COVERED
                    else _gap_text(language, coverage, len(support_identities))
                ),
            )
        )

    source_count = len({item.source_id for item in evidence})
    total_identities = len(all_identities)
    quality_notes: list[str] = []
    if duplicate_labels:
        quality_notes.append(
            "Duplicate source identities were detected; independence was adjusted: "
            + "; ".join(duplicate_labels[:10])
        )
    missing_dates = sum(
        1
        for source in sources_by_id.values()
        if source is not None and _published_year(source) is None
    )
    if missing_dates:
        quality_notes.append(
            f"{missing_dates} source(s) have no publication date; recency is unknown."
        )
    primary_count = sum(
        1
        for source in sources_by_id.values()
        if source is not None
        and assess_source_quality(source, now_year=now_year).is_primary
    )
    if sources_by_id:
        quality_notes.append(
            f"{primary_count} of {len(sources_by_id)} source(s) are classified as primary or scholarly."
        )

    coverage_ratio = round(
        (
            sum(
                1.0
                if row.coverage_status is SectionCoverageStatus.COVERED
                else 0.5
                if row.coverage_status is SectionCoverageStatus.PARTIAL
                else 0.0
                for row in rows
            )
            / len(rows)
        )
        if rows
        else 0.0,
        4,
    )

    target_ratio = round(
        (
            sum(row.evidence_ratio for row in rows if row.target_evidence > 0)
            / len([row for row in rows if row.target_evidence > 0])
        )
        if any(row.target_evidence > 0 for row in rows)
        else 0.0,
        4,
    )

    return EvidenceMatrix(
        rows=rows,
        total_evidence=len(evidence),
        source_count=source_count,
        independent_source_count=total_identities,
        contradiction_count=sum(
            1 for row in rows if row.support_count and row.refute_count
        ),
        coverage_ratio=coverage_ratio,
        target_ratio=target_ratio,
        duplicate_source_groups=duplicate_labels[:20],
        quality_notes=quality_notes,
    )


def _published_year(source: Any) -> int | None:
    published_at = getattr(source, "published_at", None)
    if published_at is not None:
        return published_at.year
    year = getattr(source, "published_year", None)
    return int(year) if year else None


def _source_type_reason(source_type: SourceType, *, primary: bool) -> str:
    if primary:
        return (
            f"The source type '{source_type.value}' indicates primary or scholarly "
            "material."
        )
    return f"The source type '{source_type.value}' does not establish primary material."


def _conclusion(
    *,
    support_count: int,
    refute_count: int,
    support_identities: int,
    refute_identities: int,
    coverage: SectionCoverageStatus,
    language: ProjectLanguage,
) -> str:
    zh = language is ProjectLanguage.ZH
    if support_count == 0 and refute_count == 0:
        return (
            "尚未提取到可支持该章节的证据。"
            if zh
            else "No evidence has been extracted for this section yet."
        )
    if refute_count and support_count:
        return (
            f"证据存在分歧：{support_identities} 个独立来源提供支持，"
            f"{refute_identities} 个独立来源提出相反证据，结论需谨慎。"
            if zh
            else (
                f"Evidence is mixed: {support_identities} independent source(s) "
                f"support the section while {refute_identities} raise contrary "
                "evidence; the conclusion is provisional."
            )
        )
    if refute_count and not support_count:
        return (
            "现有证据主要反对该章节的肯定性表述，尚不能形成支持结论。"
            if zh
            else (
                "Available evidence mainly opposes the affirmative claim; no "
                "supporting conclusion can be drawn."
            )
        )
    if coverage is SectionCoverageStatus.COVERED:
        return (
            f"{support_identities} 个独立来源提供支持证据，可形成初步结论，"
            "但仍受来源质量与适用条件限制。"
            if zh
            else (
                f"{support_identities} independent sources support a provisional "
                "conclusion, subject to source quality and applicability limits."
            )
        )
    return (
        "仅有有限或单一来源支持该章节，结论为初步判断。"
        if zh
        else "Support is limited or comes from a single source; the conclusion is provisional."
    )


def _gap_text(
    language: ProjectLanguage,
    coverage: SectionCoverageStatus,
    support_identities: int,
) -> str:
    if language is ProjectLanguage.ZH:
        if coverage is SectionCoverageStatus.INSUFFICIENT:
            return "该章节缺少支持证据，需要补充检索或调整研究范围。"
        return f"该章节仅有 {support_identities} 个独立支持来源，证据仍不充分。"
    if coverage is SectionCoverageStatus.INSUFFICIENT:
        return "The section lacks supporting evidence; more search or scope adjustment is needed."
    return (
        f"The section has only {support_identities} independent supporting "
        "source(s); evidence remains incomplete."
    )
