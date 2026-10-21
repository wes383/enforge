import hashlib
import json
import logging
from uuid import UUID, uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.services.dispatch import dispatch_simulated_failure
from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.storage import (
    StorageError,
    StorageFileNotFound,
    get_file_storage,
    smoke_original_key,
    smoke_result_key,
)
from dr_core.tasks import DEBUG_PING_TASK, STORAGE_SMOKE_TASK, get_celery_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dev", tags=["dev"])

_ALLOWED_SMOKE_CONTENT_TYPES = {
    "application/json",
    "application/octet-stream",
    "application/pdf",
    "text/markdown",
    "text/plain",
}
_UPLOAD_CHUNK_SIZE = 1024 * 1024


class SmokeUploadResponse(BaseModel):
    file_id: UUID
    storage_key: str
    sha256: str
    size_bytes: int
    task_id: str


class TaskDispatchResponse(BaseModel):
    task_id: str


def _enqueue(task_name: str, args: list[str]) -> str:
    settings = get_settings()
    result = get_celery_client().send_task(
        task_name,
        args=args,
        queue=settings.celery_task_default_queue,
    )
    return result.id


@router.post(
    "/storage-smoke",
    response_model=SmokeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_storage_smoke(file: UploadFile = File(...)) -> SmokeUploadResponse:
    settings = get_settings()
    if file.content_type and file.content_type not in _ALLOWED_SMOKE_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported content type for storage smoke endpoint",
        )

    digest = hashlib.sha256()
    payload = bytearray()
    try:
        while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
            payload.extend(chunk)
            if len(payload) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Upload exceeds the configured size limit",
                )
            digest.update(chunk)
    finally:
        await file.close()

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    file_id = uuid4()
    storage_key = smoke_original_key(file_id)
    storage = get_file_storage()
    try:
        await run_in_threadpool(storage.save, storage_key, bytes(payload))
    except StorageError as exc:
        logger.exception("Failed to persist storage smoke upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist uploaded file",
        ) from exc

    try:
        task_id = await run_in_threadpool(_enqueue, STORAGE_SMOKE_TASK, [str(file_id), digest.hexdigest()])
    except Exception as exc:
        await run_in_threadpool(storage.delete, storage_key)
        logger.exception("Failed to enqueue storage smoke task")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is unavailable",
        ) from exc

    return SmokeUploadResponse(
        file_id=file_id,
        storage_key=storage_key,
        sha256=digest.hexdigest(),
        size_bytes=len(payload),
        task_id=task_id,
    )


@router.get("/storage-smoke/{file_id}")
async def get_storage_smoke(file_id: UUID) -> JSONResponse:
    storage = get_file_storage()
    try:
        raw = await run_in_threadpool(storage.read, smoke_result_key(file_id))
    except StorageFileNotFound:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"file_id": str(file_id), "status": "pending"},
        )
    except StorageError as exc:
        logger.exception("Failed to read storage smoke result")
        raise HTTPException(status_code=500, detail="Failed to read worker result") from exc
    try:
        return JSONResponse(content=json.loads(raw))
    except json.JSONDecodeError as exc:
        logger.error("Worker result is not valid JSON", exc_info=True)
        raise HTTPException(status_code=500, detail="Worker result is invalid") from exc


@router.get("/storage-smoke/{file_id}/original")
async def download_storage_smoke(file_id: UUID) -> Response:
    storage = get_file_storage()
    try:
        raw = await run_in_threadpool(storage.read, smoke_original_key(file_id))
    except StorageFileNotFound as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    except StorageError as exc:
        logger.exception("Failed to read storage smoke original")
        raise HTTPException(status_code=500, detail="Failed to read file") from exc
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_id}.bin"'},
    )


@router.delete("/storage-smoke/{file_id}")
async def delete_storage_smoke(file_id: UUID) -> dict[str, object]:
    storage = get_file_storage()
    deleted_original = await run_in_threadpool(storage.delete, smoke_original_key(file_id))
    deleted_result = await run_in_threadpool(storage.delete, smoke_result_key(file_id))
    return {
        "file_id": str(file_id),
        "deleted_original": deleted_original,
        "deleted_result": deleted_result,
    }


@router.post(
    "/ping",
    response_model=TaskDispatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch_ping() -> TaskDispatchResponse:
    try:
        task_id = await run_in_threadpool(_enqueue, DEBUG_PING_TASK, [])
    except Exception as exc:
        logger.exception("Failed to enqueue ping task")
        raise HTTPException(status_code=503, detail="Task queue is unavailable") from exc
    return TaskDispatchResponse(task_id=task_id)


@router.post(
    "/runs/{run_id}/fail",
    response_model=TaskDispatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def dispatch_simulated_run_failure(run_id: UUID) -> TaskDispatchResponse:
    with get_database().session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Research run not found")
        project = session.get(ResearchProject, run.project_id)
        if project is None or project.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Research run not found")
        project_id = run.project_id

    try:
        task_id = await run_in_threadpool(
            dispatch_simulated_failure,
            run_id,
            project_id,
        )
    except Exception as exc:
        logger.exception("Failed to dispatch simulated failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is unavailable",
        ) from exc
    return TaskDispatchResponse(task_id=task_id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: UUID) -> dict[str, object]:
    result = AsyncResult(str(task_id), app=get_celery_client())
    payload: dict[str, object] = {"task_id": str(task_id), "state": result.state}
    if result.state == "SUCCESS":
        payload["result"] = result.result
    elif result.state == "FAILURE":
        payload["error"] = type(result.result).__name__
    return payload
