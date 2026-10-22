import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException

from app.api.schemas import FileListResponse, FileUploadResponse
from app.services.dispatch import dispatch_document_parse
from app.services.uploads import (
    FileRecord,
    UploadValidationError,
    content_disposition,
    get_file_record,
    iter_file,
    list_project_files as list_uploaded_files,
    open_download,
    sanitize_original_filename,
    store_upload,
    validate_upload,
)
from dr_core.config import get_settings
from dr_core.storage import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])

_READ_CHUNK_SIZE = 1024 * 1024


def _file_response(record: FileRecord) -> FileUploadResponse:
    return FileUploadResponse(
        file_id=record.file_id,
        source_id=record.source_id,
        project_id=record.project_id,
        original_name=record.original_name,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        parser_status=record.parser_status,
        parser_version=record.parser_version,
        parse_error=record.parse_error,
        created_at=record.created_at,
    )


@router.post(
    "/projects/{project_id}/files",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_file(
    project_id: UUID,
    request: Request,
) -> FileUploadResponse:
    settings = get_settings()
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "multipart/form-data":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Type must be multipart/form-data",
        )

    try:
        form = await request.form(
            max_files=1,
            max_fields=0,
            max_part_size=settings.max_upload_bytes + 1024 * 1024,
        )
    except MultiPartException as exc:
        too_large = "size" in str(exc).lower()
        raise HTTPException(
            status_code=(
                status.HTTP_413_CONTENT_TOO_LARGE
                if too_large
                else status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Upload exceeds the configured size limit"
                if too_large
                else "Invalid multipart upload"
            ),
        ) from exc

    try:
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="multipart field 'file' is required",
            )

        payload = bytearray()
        while chunk := await upload.read(_READ_CHUNK_SIZE):
            payload.extend(chunk)
            if len(payload) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Upload exceeds the configured size limit",
                )

        try:
            upload_kind = validate_upload(upload.content_type, bytes(payload))
        except UploadValidationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            ) from exc

        original_name = sanitize_original_filename(upload.filename)
    finally:
        await form.close()

    try:
        stored = await run_in_threadpool(
            store_upload,
            project_id,
            original_name,
            upload_kind,
            bytes(payload),
        )
    except StorageError as exc:
        logger.exception("Failed to store upload for project %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded file",
        ) from exc

    try:
        await run_in_threadpool(
            dispatch_document_parse,
            stored.file_id,
            stored.project_id,
        )
    except Exception:
        logger.warning(
            "Failed to dispatch parse task for file %s",
            stored.file_id,
            exc_info=True,
        )

    return _file_response(stored)


@router.get(
    "/projects/{project_id}/files",
    response_model=FileListResponse,
)
async def list_project_files(project_id: UUID) -> FileListResponse:
    records = await run_in_threadpool(list_uploaded_files, project_id)
    return FileListResponse(items=[_file_response(record) for record in records])


@router.get("/files/{file_id}", response_model=FileUploadResponse)
async def get_project_file(file_id: UUID) -> FileUploadResponse:
    record = await run_in_threadpool(get_file_record, file_id)
    return _file_response(record)


@router.get("/files/{file_id}/download")
async def download_project_file(file_id: UUID) -> StreamingResponse:
    download = await run_in_threadpool(open_download, file_id)
    return StreamingResponse(
        iter_file(download.handle),
        media_type=download.mime_type,
        headers={
            "Content-Disposition": content_disposition(
                download.filename,
                download.mime_type,
            ),
            "Content-Length": str(download.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
