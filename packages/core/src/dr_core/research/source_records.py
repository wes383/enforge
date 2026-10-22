from __future__ import annotations

from uuid import UUID

from dr_core.db.enums import SourceOrigin, SourceType
from dr_core.normalization import normalize_doi, normalize_url
from dr_core.research.models import SourceRecord


def web_source_record(ingested, result, question_id: UUID | None) -> SourceRecord:
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
        question_ids=[question_id] if question_id else [],
        rank=result.rank,
    )


def academic_source_record(
    ingested,
    result,
    question_id: UUID | None,
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
        question_ids=[question_id] if question_id else [],
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
    merged_questions = list(
        dict.fromkeys([*existing.question_ids, *candidate.question_ids])
    )
    if merged_questions != existing.question_ids:
        updates["question_ids"] = merged_questions
    return existing.model_copy(update=updates) if updates else existing


def source_keys(record: SourceRecord) -> list[str]:
    keys = [f"id:{record.source_id}"]
    if record.doi:
        try:
            keys.append(f"doi:{normalize_doi(record.doi)}")
        except ValueError:
            pass
    if record.url:
        try:
            keys.append(f"url:{normalize_url(record.url)}")
        except ValueError:
            pass
    return keys


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
