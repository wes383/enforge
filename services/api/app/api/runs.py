import asyncio
import json
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.schemas import EventListResponse, EventResponse, RunResponse
from app.dependencies import SessionDep
from app.services.dispatch import dispatch_research_run, revoke_research_run
from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.enums import TERMINAL_RUN_STATUSES, RunStatus
from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.db.repositories import (
    ProjectRepository,
    RunEventRepository,
    RunRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.schemas import RunCreate
from dr_core.services import (
    ActiveRunLimitExceeded,
    create_run_with_event,
    fail_run,
    request_run_cancel,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runs"])

_SSE_PAGE_SIZE = 500


def _ensure_run_visible(run_id: UUID) -> None:
    with get_database().session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        project = session.get(ResearchProject, run.project_id)
        if project is None or project.deleted_at is not None:
            raise EntityNotFound("Research run not found")


def _fetch_event_page(
    run_id: UUID,
    after_id: int,
) -> tuple[list[dict], RunStatus]:
    with get_database().session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        project = session.get(ResearchProject, run.project_id)
        if project is None or project.deleted_at is not None:
            raise EntityNotFound("Research run not found")

        events = RunEventRepository(session).list_after(
            run.project_id,
            run.id,
            after_id=after_id,
            limit=_SSE_PAGE_SIZE,
        )
        payloads = [
            EventResponse.model_validate(event).model_dump(mode="json")
            for event in events
        ]
        return payloads, run.status


@router.post(
    "/projects/{project_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    project_id: UUID,
    response: Response,
    body: Annotated[RunCreate | None, Body()] = None,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> RunResponse:
    if idempotency_key is not None and len(idempotency_key) > 128:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be at most 128 characters",
        )

    payload = body or RunCreate()
    effective_key = idempotency_key or payload.idempotency_key
    try:
        run, created = create_run_with_event(
            get_database(),
            project_id,
            RunCreate(idempotency_key=effective_key),
            max_active_runs=get_settings().max_active_runs_per_project,
        )
    except ActiveRunLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc

    if created:
        try:
            dispatch_research_run(run.id, run.project_id)
        except Exception as exc:
            logger.exception("Failed to dispatch research run %s", run.id)
            fail_run(
                get_database(),
                run.id,
                run.project_id,
                "Task queue is unavailable; the run was not dispatched",
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "Task queue is unavailable",
                    "run_id": str(run.id),
                },
            ) from exc
    else:
        response.status_code = status.HTTP_200_OK

    return RunResponse.model_validate(run)


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, session: SessionDep) -> RunResponse:
    run = RunRepository(session).get(run_id)
    ProjectRepository(session).get(run.project_id)
    return RunResponse.model_validate(run)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_run(run_id: UUID, session: SessionDep) -> RunResponse:
    run = RunRepository(session).get(run_id)
    ProjectRepository(session).get(run.project_id)

    outcome = request_run_cancel(
        get_database(),
        run_id,
        project_id=run.project_id,
    )
    try:
        revoke_research_run(run_id)
    except Exception:
        logger.warning(
            "Celery revoke failed for run %s; database cancellation still applies",
            run_id,
            exc_info=True,
        )

    return RunResponse.model_validate(outcome.run)


@router.get("/runs/{run_id}/events", response_model=EventListResponse)
def list_run_events(
    run_id: UUID,
    session: SessionDep,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> EventListResponse:
    run = RunRepository(session).get(run_id)
    ProjectRepository(session).get(run.project_id)
    events = RunEventRepository(session).list_after(
        run.project_id,
        run.id,
        after_id=after_id,
        limit=limit,
    )
    return EventListResponse(
        items=[EventResponse.model_validate(event) for event in events]
    )


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID"),
    ] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    await run_in_threadpool(_ensure_run_visible, run_id)

    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID must be an integer",
            ) from exc
        if cursor < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Last-Event-ID must be non-negative",
            )
    else:
        cursor = after

    settings = get_settings()

    async def event_stream():
        nonlocal cursor
        last_output = time.monotonic()
        yield "retry: 3000\n\n"

        while True:
            if await request.is_disconnected():
                return

            try:
                events, run_status = await run_in_threadpool(
                    _fetch_event_page,
                    run_id,
                    cursor,
                )
            except EntityNotFound:
                return

            for event in events:
                yield (
                    f"id: {event['id']}\n"
                    f"event: {event['event_type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )
                cursor = int(event["id"])
                last_output = time.monotonic()

            if events and len(events) == _SSE_PAGE_SIZE:
                continue
            if run_status in TERMINAL_RUN_STATUSES:
                return

            now = time.monotonic()
            if now - last_output >= settings.sse_keepalive_seconds:
                yield ": keepalive\n\n"
                last_output = now

            await asyncio.sleep(settings.sse_poll_interval_seconds)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
