from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from dr_core.db import Database
from dr_core.db.enums import (
    ParserStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import SourceVersion
from dr_core.db.repositories import (
    ProjectRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.normalization import text_sha256
from dr_core.providers.models import (
    AcademicResult,
    FetchStatus,
    ParsedDocument,
    SearchResult,
    WebFetchResult,
)
from dr_core.schemas import SourceCreate, SourceVersionCreate
from dr_core.storage import FileStorage, StorageError, get_file_storage


@dataclass(frozen=True, slots=True)
class IngestedSource:
    source_id: UUID
    version_id: UUID | None
    content_kind: SourceVersionKind | None
    parser_status: ParserStatus | None


@dataclass(frozen=True, slots=True)
class PersistedFetch:
    source_id: UUID
    version_id: UUID | None
    status: FetchStatus
    content_kind: SourceVersionKind | None
    storage_key: str | None


@dataclass(frozen=True, slots=True)
class ParsePersistence:
    version_id: UUID
    parser_status: ParserStatus


def ingest_web_search_result(
    database: Database,
    project_id: UUID,
    result: SearchResult,
    *,
    query: str | None = None,
) -> IngestedSource:
    with database.session_scope() as session:
        ProjectRepository(session).get(project_id)
        source = SourceRepository(session).get_or_create(
            project_id,
            SourceCreate(
                title=result.title,
                url=result.url,
                source_type=SourceType.WEB_PAGE,
                source_origin=SourceOrigin.WEB_SEARCH,
                metadata={
                    "discovered_by": "web_search",
                    "provider": result.provider.value,
                    "score": result.score,
                    "rank": result.rank,
                },
            ),
        )
        version = None
        snippet = result.snippet.strip()
        if snippet:
            version = SourceVersionRepository(session).create(
                project_id,
                source.id,
                SourceVersionCreate(
                    content_kind=SourceVersionKind.SEARCH_SNIPPET,
                    content_hash=text_sha256(snippet),
                    mime_type="text/plain",
                    size_bytes=len(snippet.encode("utf-8")),
                    metadata={
                        "provider": result.provider.value,
                        "query": query,
                        "rank": result.rank,
                        "score": result.score,
                        "published_at": (
                            result.published_at.isoformat()
                            if result.published_at
                            else None
                        ),
                        "snippet": snippet[:20000],
                    },
                ),
            )
            if version.parser_status is ParserStatus.PENDING:
                version.parser_status = ParserStatus.SKIPPED
                session.flush()
        return IngestedSource(
            source_id=source.id,
            version_id=version.id if version else None,
            content_kind=version.content_kind if version else None,
            parser_status=version.parser_status if version else None,
        )


def ingest_academic_result(
    database: Database,
    project_id: UUID,
    result: AcademicResult,
) -> IngestedSource:
    with database.session_scope() as session:
        ProjectRepository(session).get(project_id)
        source = SourceRepository(session).get_or_create(
            project_id,
            SourceCreate(
                title=result.title,
                authors=list(result.authors),
                published_at=result.published_at,
                doi=result.doi,
                url=result.url,
                source_type=_academic_source_type(result.work_type),
                source_origin=SourceOrigin.ACADEMIC_SEARCH,
                venue=result.venue,
                publisher=result.publisher,
                metadata={
                    "discovered_by": "academic_search",
                    "provider": result.provider.value,
                    "provider_id": result.provider_id,
                    "published_year": result.published_year,
                    "citation_count": result.citation_count,
                    "reference_count": result.reference_count,
                    "open_access_url": result.open_access_url,
                    "work_type": result.work_type,
                },
            ),
        )
        version = None
        abstract = (result.abstract or "").strip()
        if abstract:
            version = SourceVersionRepository(session).create(
                project_id,
                source.id,
                SourceVersionCreate(
                    content_kind=SourceVersionKind.SEARCH_SNIPPET,
                    content_hash=text_sha256(abstract),
                    mime_type="text/plain",
                    size_bytes=len(abstract.encode("utf-8")),
                    metadata={
                        "provider": result.provider.value,
                        "provider_id": result.provider_id,
                        "snippet": abstract[:100000],
                        "completeness": "abstract",
                        "citation_count": result.citation_count,
                    },
                ),
            )
            if version.parser_status is ParserStatus.PENDING:
                version.parser_status = ParserStatus.SKIPPED
                session.flush()
        return IngestedSource(
            source_id=source.id,
            version_id=version.id if version else None,
            content_kind=version.content_kind if version else None,
            parser_status=version.parser_status if version else None,
        )


def persist_web_fetch(
    database: Database,
    project_id: UUID,
    source_id: UUID,
    result: WebFetchResult,
    *,
    storage: FileStorage | None = None,
) -> PersistedFetch:
    storage = storage or get_file_storage()
    fetched_at = result.fetched_at.isoformat()
    fetch_metadata = {
        "provider": result.provider.value,
        "url": result.url,
        "final_url": result.final_url,
        "status": result.status.value,
        "http_status": result.http_status,
        "fetched_at": fetched_at,
        "title": result.title,
        "error_code": result.error_code,
        "error_message": result.error_message,
    }

    content_hash = result.content_hash or text_sha256(result.text)
    storage_key: str | None = None
    if result.status is FetchStatus.SUCCESS and result.text.strip():
        storage_key = (
            f"projects/{project_id}/sources/{source_id}/fetched/"
            f"{content_hash[:16]}.md"
        )
        storage.save(storage_key, result.text.encode("utf-8"))

    try:
        with database.session_scope() as session:
            ProjectRepository(session).get(project_id)
            source = SourceRepository(session).get(source_id, project_id=project_id)
            source.source_metadata = {
                **source.source_metadata,
                "last_fetch": fetch_metadata,
            }

            version: SourceVersion | None = None
            if storage_key is not None:
                version = SourceVersionRepository(session).create(
                    project_id,
                    source_id,
                    SourceVersionCreate(
                        content_kind=SourceVersionKind.FULL_TEXT,
                        content_hash=content_hash,
                        storage_key=storage_key,
                        mime_type="text/markdown",
                        size_bytes=len(result.text.encode("utf-8")),
                        fetched_url=result.final_url or result.url,
                        metadata={
                            **fetch_metadata,
                            "completeness": "full_text",
                            "description": result.description,
                            **result.metadata,
                        },
                    ),
                )
            elif result.text.strip():
                version = SourceVersionRepository(session).create(
                    project_id,
                    source_id,
                    SourceVersionCreate(
                        content_kind=SourceVersionKind.SEARCH_SNIPPET,
                        content_hash=content_hash,
                        mime_type="text/plain",
                        size_bytes=len(result.text.encode("utf-8")),
                        fetched_url=result.final_url or result.url,
                        metadata={
                            **fetch_metadata,
                            "completeness": "abstract_only",
                            "snippet": result.text[:20000],
                            "description": result.description,
                        },
                    ),
                )
                if version.parser_status is ParserStatus.PENDING:
                    version.parser_status = ParserStatus.SKIPPED
                    session.flush()

            return PersistedFetch(
                source_id=source_id,
                version_id=version.id if version else None,
                status=result.status,
                content_kind=version.content_kind if version else None,
                storage_key=storage_key,
            )
    except Exception:
        if storage_key is not None:
            try:
                storage.delete(storage_key)
            except StorageError:
                pass
        raise


def mark_parse_started(
    database: Database,
    project_id: UUID,
    version_id: UUID,
    *,
    parser_version: str,
) -> ParsePersistence:
    with database.session_scope() as session:
        ProjectRepository(session).get(project_id)
        version = SourceVersionRepository(session).mark_parsing(
            version_id,
            project_id=project_id,
            parser_version=parser_version,
        )
        return ParsePersistence(
            version_id=version.id,
            parser_status=version.parser_status,
        )


def persist_parsed_document(
    database: Database,
    project_id: UUID,
    version_id: UUID,
    parsed: ParsedDocument,
) -> ParsePersistence:
    parse_metadata = {
        "parse": {
            "status": parsed.status.value,
            "title": parsed.title,
            "page_count": parsed.page_count,
            "paragraph_count": parsed.paragraph_count,
            "section_count": len(parsed.sections),
            "character_count": parsed.character_count,
            "tables_status": parsed.tables_status,
            "table_count": len(parsed.tables),
            "truncated": parsed.truncated,
            "parser_name": parsed.parser_name,
            "parser_version": parsed.parser_version,
            "parsed_at": datetime.now(tz=UTC).isoformat(),
            **parsed.metadata,
        }
    }
    with database.session_scope() as session:
        ProjectRepository(session).get(project_id)
        version = SourceVersionRepository(session).mark_parsed(
            version_id,
            project_id=project_id,
            parser_version=parsed.parser_version,
            metadata_updates=parse_metadata,
        )
        return ParsePersistence(
            version_id=version.id,
            parser_status=version.parser_status,
        )


def persist_parse_failure(
    database: Database,
    project_id: UUID,
    version_id: UUID,
    *,
    parser_version: str,
    error: str,
    error_code: str | None = None,
) -> ParsePersistence:
    with database.session_scope() as session:
        ProjectRepository(session).get(project_id)
        version = SourceVersionRepository(session).mark_parse_failed(
            version_id,
            project_id=project_id,
            parser_version=parser_version,
            error=error,
            metadata_updates={
                "parse": {
                    "status": "failed",
                    "error": error[:4000],
                    "error_code": error_code,
                    "parser_version": parser_version,
                    "failed_at": datetime.now(tz=UTC).isoformat(),
                }
            },
        )
        return ParsePersistence(
            version_id=version.id,
            parser_status=version.parser_status,
        )


def _academic_source_type(work_type: str | None) -> SourceType:
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
