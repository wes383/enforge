import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from dr_core.db import get_database
from dr_core.db.enums import (
    ParserStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.models import ResearchProject, Source, SourceVersion
from dr_core.db.repositories import (
    ProjectRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.schemas import SourceCreate, SourceVersionCreate
from dr_core.storage import (
    StorageError,
    StorageFileNotFound,
    get_file_storage,
)

logger = logging.getLogger(__name__)

_UPLOAD_TYPES: dict[str, tuple[str, Literal["pdf", "html", "markdown", "text"]]] = {
    "application/pdf": (".pdf", "pdf"),
    "text/html": (".html", "html"),
    "application/xhtml+xml": (".html", "html"),
    "text/markdown": (".md", "markdown"),
    "text/x-markdown": (".md", "markdown"),
    "text/plain": (".txt", "text"),
}


class UploadValidationError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class UploadKind:
    mime_type: str
    extension: str
    kind: Literal["pdf", "html", "markdown", "text"]


@dataclass(frozen=True, slots=True)
class StoredUpload:
    file_id: UUID
    source_id: UUID
    project_id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    parser_status: ParserStatus
    parser_version: str | None
    parse_error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FileRecord:
    file_id: UUID
    source_id: UUID
    project_id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    parser_status: ParserStatus
    parser_version: str | None
    parse_error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadHandle:
    handle: BinaryIO
    size_bytes: int
    mime_type: str
    filename: str


def sanitize_original_filename(raw: str | None) -> str:
    candidate = (raw or "upload").replace("\\", "/").split("/")[-1]
    candidate = "".join(
        character
        for character in candidate
        if character.isprintable() and character not in {'"', "\r", "\n"}
    ).strip().strip(".")
    if not candidate:
        candidate = "upload"
    if len(candidate) > 255:
        stem, dot, extension = candidate.rpartition(".")
        if dot and len(extension) <= 16:
            candidate = f"{stem[: 255 - len(extension) - 1]}.{extension}"
        else:
            candidate = candidate[:255]
    return candidate


def validate_upload(content_type: str | None, content: bytes) -> UploadKind:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    allowed = _UPLOAD_TYPES.get(media_type)
    if allowed is None:
        raise UploadValidationError(
            415,
            "Unsupported file type; allowed: PDF, HTML, Markdown, plain text",
        )
    if not content:
        raise UploadValidationError(400, "Uploaded file is empty")

    extension, kind = allowed
    if kind == "pdf":
        if not content.startswith(b"%PDF-"):
            raise UploadValidationError(400, "File does not contain a PDF signature")
    else:
        if b"\x00" in content:
            raise UploadValidationError(400, "Text documents must not contain NUL bytes")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadValidationError(
                400,
                "Text documents must be valid UTF-8",
            ) from exc

    return UploadKind(mime_type=media_type, extension=extension, kind=kind)


def store_upload(
    project_id: UUID,
    original_name: str,
    upload: UploadKind,
    content: bytes,
) -> StoredUpload:
    storage = get_file_storage()
    digest = hashlib.sha256(content).hexdigest()
    storage_key = (
        f"projects/{project_id}/sources/{uuid4().hex}/original{upload.extension}"
    )

    storage.save(storage_key, content)
    try:
        with get_database().session_scope() as session:
            ProjectRepository(session).get(project_id)
            source = SourceRepository(session).create(
                project_id,
                SourceCreate(
                    title=original_name,
                    source_type=SourceType.USER_DOCUMENT,
                    source_origin=SourceOrigin.USER_UPLOAD,
                    metadata={
                        "original_filename": original_name,
                        "uploaded_mime_type": upload.mime_type,
                    },
                ),
            )
            version = SourceVersionRepository(session).create(
                project_id,
                source.id,
                SourceVersionCreate(
                    content_kind=SourceVersionKind.UPLOADED_DOCUMENT,
                    content_hash=digest,
                    storage_key=storage_key,
                    mime_type=upload.mime_type,
                    size_bytes=len(content),
                    metadata={"original_filename": original_name},
                ),
            )
            return StoredUpload(
                file_id=version.id,
                source_id=source.id,
                project_id=project_id,
                original_name=original_name,
                mime_type=upload.mime_type,
                size_bytes=len(content),
                sha256=digest,
                parser_status=version.parser_status,
                parser_version=version.parser_version,
                parse_error=version.parse_error,
                created_at=version.created_at,
            )
    except Exception:
        try:
            storage.delete(storage_key)
        except StorageError:
            logger.exception("Failed to remove orphaned upload %s", storage_key)
        raise


def get_file_record(file_id: UUID) -> FileRecord:
    with get_database().session() as session:
        version = session.get(SourceVersion, file_id)
        if version is None:
            raise EntityNotFound("File not found")
        project = session.get(ResearchProject, version.project_id)
        if project is None or project.deleted_at is not None:
            raise EntityNotFound("File not found")
        source = session.get(Source, version.source_id)
        original_name = sanitize_original_filename(
            version.version_metadata.get("original_filename")
            or (source.title if source else None)
        )
        return FileRecord(
            file_id=version.id,
            source_id=version.source_id,
            project_id=version.project_id,
            original_name=original_name,
            mime_type=version.mime_type or "application/octet-stream",
            size_bytes=version.size_bytes or 0,
            sha256=version.content_hash,
            parser_status=version.parser_status,
            parser_version=version.parser_version,
            parse_error=version.parse_error,
            created_at=version.created_at,
        )


def open_download(file_id: UUID) -> DownloadHandle:
    with get_database().session() as session:
        version = session.get(SourceVersion, file_id)
        if version is None or not version.storage_key:
            raise EntityNotFound("File not found")
        project = session.get(ResearchProject, version.project_id)
        if project is None or project.deleted_at is not None:
            raise EntityNotFound("File not found")
        source = session.get(Source, version.source_id)
        filename = sanitize_original_filename(source.title if source else None)
        storage_key = version.storage_key
        mime_type = version.mime_type or "application/octet-stream"

    storage = get_file_storage()
    try:
        stored = storage.stat(storage_key)
        handle = storage.open(storage_key)
    except StorageFileNotFound as exc:
        raise EntityNotFound("File content is missing") from exc

    return DownloadHandle(
        handle=handle,
        size_bytes=stored.size_bytes,
        mime_type=mime_type,
        filename=filename,
    )


def content_disposition(filename: str, mime_type: str) -> str:
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    fallback = f"download{extension}"
    encoded = quote(filename, safe="")
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


def iter_file(handle: BinaryIO):
    try:
        while chunk := handle.read(1024 * 1024):
            yield chunk
    finally:
        handle.close()
