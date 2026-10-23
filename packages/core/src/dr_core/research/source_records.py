from __future__ import annotations

import re
from uuid import UUID

from dr_core.db.enums import SourceOrigin, SourceType
from dr_core.normalization import normalize_doi, normalize_url
from dr_core.research.models import SourceRecord

# Preprint servers mint a new DOI/URL per revision (``..._v2``, ``/v3``,
# arXiv's ``2101.00001v2``). Those are the same work, so duplicate detection
# compares the versionless form while the stored identifier stays untouched.
_VERSION_SUFFIX_RE = re.compile(r"(?:[._/-]?v\d+)$", re.IGNORECASE)


def _versionless(value: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", value)


def web_source_record(ingested, result, section_id: UUID | None) -> SourceRecord:
    return SourceRecord(
        source_id=ingested.source_id,
        source_version_id=ingested.version_id,
        title=result.title,
        url=result.url,
        fetch_url=result.url,
        authors=[],
        published_at=result.published_at,
        source_type=SourceType.WEB_PAGE,
        source_origin=SourceOrigin.WEB_SEARCH,
        provider=result.provider.value,
        score=result.score,
        snippet=result.snippet,
        has_full_text=False,
        parser_status=(
            ingested.parser_status.value if ingested.parser_status else None
        ),
        section_ids=[section_id] if section_id else [],
        rank=result.rank,
    )


def academic_source_record(
    ingested,
    result,
    section_id: UUID | None,
) -> SourceRecord:
    return SourceRecord(
        source_id=ingested.source_id,
        source_version_id=ingested.version_id,
        title=result.title,
        url=result.url,
        fetch_url=result.open_access_url or result.url,
        doi=result.doi,
        authors=list(result.authors),
        published_at=result.published_at,
        source_type=academic_source_type(result.work_type),
        source_origin=SourceOrigin.ACADEMIC_SEARCH,
        provider=result.provider.value,
        score=result.score,
        snippet=result.abstract,
        has_full_text=False,
        parser_status=(
            ingested.parser_status.value if ingested.parser_status else None
        ),
        section_ids=[section_id] if section_id else [],
        rank=result.rank,
    )


def merge_source_record(
    existing: SourceRecord,
    candidate: SourceRecord,
) -> SourceRecord:
    updates = {}
    for field in ("doi", "url", "fetch_url", "authors", "published_at", "snippet"):
        if not getattr(existing, field) and getattr(candidate, field):
            updates[field] = getattr(candidate, field)
    if not existing.source_version_id and candidate.source_version_id:
        updates["source_version_id"] = candidate.source_version_id
    if existing.score is None or (
        candidate.score is not None and candidate.score > existing.score
    ):
        updates["score"] = candidate.score
    if existing.rank_score is None or (
        candidate.rank_score is not None
        and candidate.rank_score > (existing.rank_score or 0)
    ):
        updates["rank_score"] = candidate.rank_score
    if candidate.has_full_text and not existing.has_full_text:
        updates["has_full_text"] = True
        updates["source_version_id"] = candidate.source_version_id
        updates["parser_status"] = candidate.parser_status
    merged_sections = list(
        dict.fromkeys([*existing.section_ids, *candidate.section_ids])
    )
    if merged_sections != existing.section_ids:
        updates["section_ids"] = merged_sections
    return existing.model_copy(update=updates) if updates else existing


def source_keys(record: SourceRecord) -> list[str]:
    keys = [f"id:{record.source_id}"]
    if record.doi:
        try:
            doi = normalize_doi(record.doi)
        except ValueError:
            pass
        else:
            keys.append(f"doi:{doi}")
            versionless = _versionless(doi)
            if versionless != doi:
                keys.append(f"doi-versionless:{versionless}")
    if record.url:
        try:
            url = normalize_url(record.url)
        except ValueError:
            pass
        else:
            keys.append(f"url:{url}")
            versionless = _versionless(url)
            if versionless != url:
                keys.append(f"url-versionless:{versionless}")
    return keys


def merge_duplicate_records(records: list[SourceRecord]) -> list[SourceRecord]:
    groups: list[tuple[SourceRecord, set[str]]] = []
    for record in records:
        keys = set(source_keys(record))
        matched = [
            index
            for index, (_, group_keys) in enumerate(groups)
            if keys & group_keys
        ]
        if not matched:
            groups.append((record, keys))
            continue
        merged, merged_keys = groups[matched[0]]
        merged = merge_source_record(merged, record)
        merged_keys = merged_keys | keys
        for index in matched[1:]:
            merged = merge_source_record(merged, groups[index][0])
            merged_keys |= groups[index][1]
        groups[matched[0]] = (merged, merged_keys)
        for index in reversed(matched[1:]):
            groups.pop(index)
    return [record for record, _ in groups]


def academic_source_type(work_type: str | None) -> SourceType:
    normalized = (work_type or "").casefold()
    if "journal" in normalized:
        return SourceType.JOURNAL_ARTICLE
    if "proceedings" in normalized or "conference" in normalized:
        return SourceType.CONFERENCE_PAPER
    if "posted" in normalized or "preprint" in normalized:
        return SourceType.PREPRINT
    if "book" in normalized:
        return SourceType.BOOK
    if "report" in normalized:
        return SourceType.REPORT
    if "dataset" in normalized:
        return SourceType.DATASET
    return SourceType.WEB_PAGE
