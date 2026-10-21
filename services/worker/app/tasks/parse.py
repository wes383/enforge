import logging
from uuid import UUID

from app.celery_app import celery_app

from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.enums import ParserStatus, SourceVersionKind
from dr_core.db.repositories import (
    ProjectRepository,
    SourceVersionRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.providers import (
    DocumentParseError,
    LocalDocumentParser,
    UnsupportedDocumentType,
)
from dr_core.services import (
    mark_parse_started,
    persist_parse_failure,
    persist_parsed_document,
)
from dr_core.storage import (
    StorageError,
    StorageFileNotFound,
    get_file_storage,
)
from dr_core.tasks.names import PARSE_DOCUMENT_TASK
from dr_core.tasks.retry import retry_countdown_seconds

logger = logging.getLogger(__name__)

_settings = get_settings()
_parser = LocalDocumentParser(
    max_input_bytes=_settings.parser_max_input_bytes,
    max_output_chars=_settings.parser_max_output_chars,
    max_pdf_pages=_settings.parser_max_pdf_pages,
)
_MAX_TASK_RETRIES = 2


def _result(version_id: UUID, status: str, *, parser_version: str | None = None):
    payload = {"source_version_id": str(version_id), "status": status}
    if parser_version:
        payload["parser_version"] = parser_version
    return payload


def _record_failure(
    project_id: UUID,
    version_id: UUID,
    error: str,
    *,
    error_code: str | None = None,
) -> None:
    try:
        persist_parse_failure(
            get_database(),
            project_id,
            version_id,
            parser_version=_parser.version,
            error=error,
            error_code=error_code,
        )
    except Exception:
        logger.exception("Unable to persist parse failure for %s", version_id)


@celery_app.task(
    bind=True,
    name=PARSE_DOCUMENT_TASK,
    max_retries=_MAX_TASK_RETRIES,
    soft_time_limit=_settings.parse_task_soft_time_limit_seconds,
    time_limit=_settings.parse_task_hard_time_limit_seconds,
    acks_late=True,
    reject_on_worker_lost=True,
)
def parse_document(self, source_version_id: str, project_id: str) -> dict[str, str]:
    version_uuid = UUID(source_version_id)
    project_uuid = UUID(project_id)
    database = get_database()

    try:
        with database.session() as session:
            ProjectRepository(session).get(project_uuid)
            version = SourceVersionRepository(session).get(
                version_uuid,
                project_id=project_uuid,
            )
            if (
                version.parser_status is ParserStatus.PARSED
                and version.parser_version == _parser.version
            ):
                return _result(version_uuid, "parsed", parser_version=_parser.version)
            if (
                version.content_kind is SourceVersionKind.SEARCH_SNIPPET
                or not version.storage_key
            ):
                return _result(version_uuid, "skipped")
            mime_type = version.mime_type or ""
            filename = (
                version.version_metadata.get("original_filename")
                or version.version_metadata.get("filename")
            )
            storage_key = version.storage_key
    except EntityNotFound:
        logger.warning("Discarding parse task for missing version %s", source_version_id)
        return _result(version_uuid, "ignored")

    try:
        mark_parse_started(
            database,
            project_uuid,
            version_uuid,
            parser_version=_parser.version,
        )
    except EntityNotFound:
        logger.warning(
            "Discarding parse task for deleted version %s",
            source_version_id,
        )
        return _result(version_uuid, "ignored")

    storage = get_file_storage()
    try:
        with storage.open(storage_key) as handle:
            content = handle.read(_settings.parser_max_input_bytes + 1)
    except StorageFileNotFound:
        _record_failure(
            project_uuid,
            version_uuid,
            "Stored document is missing",
            error_code="missing_file",
        )
        return _result(version_uuid, "failed")
    except StorageError:
        attempt = self.request.retries + 1
        if self.request.retries >= _MAX_TASK_RETRIES:
            _record_failure(
                project_uuid,
                version_uuid,
                "Stored document could not be read",
                error_code="storage_error",
            )
            return _result(version_uuid, "failed")
        raise self.retry(
            exc=StorageError("Stored document could not be read"),
            countdown=retry_countdown_seconds(attempt),
        )

    if len(content) > _settings.parser_max_input_bytes:
        _record_failure(
            project_uuid,
            version_uuid,
            "Document exceeds the configured input size limit",
            error_code="input_too_large",
        )
        return _result(version_uuid, "failed")

    try:
        parsed = _parser.parse(
            content,
            mime_type=mime_type,
            filename=str(filename) if filename else None,
        )
    except (DocumentParseError, UnsupportedDocumentType) as exc:
        logger.info("Parse failed for %s: %s", version_uuid, type(exc).__name__)
        _record_failure(
            project_uuid,
            version_uuid,
            str(exc),
            error_code=exc.code,
        )
        return _result(version_uuid, "failed")
    except Exception as exc:
        logger.exception("Unexpected parser failure for %s", version_uuid)
        _record_failure(
            project_uuid,
            version_uuid,
            f"Unexpected parser failure: {type(exc).__name__}",
            error_code="unexpected_error",
        )
        raise

    persistence = persist_parsed_document(
        database,
        project_uuid,
        version_uuid,
        parsed,
    )
    logger.info(
        "Parsed %s with %s (%s chars)",
        version_uuid,
        parsed.parser_version,
        parsed.character_count,
    )
    return _result(
        version_uuid,
        persistence.parser_status.value,
        parser_version=parsed.parser_version,
    )
