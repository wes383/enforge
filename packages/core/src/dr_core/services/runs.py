from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dr_core.db import Database
from dr_core.db.enums import TERMINAL_RUN_STATUSES, RunEventType, RunStatus
from dr_core.db.models import ResearchRun
from dr_core.db.repositories import RunEventRepository, RunRepository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.schemas import RunCreate, RunEventCreate
from dr_core.services.run_state import (
    RunAlreadyFinished,
    ensure_run_transition,
)

_ERROR_SUMMARY_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    run: ResearchRun
    finalized: bool


def lock_run(
    session: Session,
    run_id: UUID,
    project_id: UUID | None = None,
) -> ResearchRun | None:
    statement = select(ResearchRun).where(ResearchRun.id == run_id)
    if project_id is not None:
        statement = statement.where(ResearchRun.project_id == project_id)
    return session.scalar(statement.with_for_update())


def create_run_with_event(
    database: Database,
    project_id: UUID,
    data: RunCreate,
) -> tuple[ResearchRun, bool]:
    with database.session_scope() as session:
        run, created = RunRepository(session).create_or_get(project_id, data)
        if created:
            RunEventRepository(session).append(
                run.project_id,
                run.id,
                RunEventCreate(
                    event_type=RunEventType.RUN_CREATED,
                    stage=RunStatus.QUEUED,
                    message="Research run queued",
                    dedupe_key="run:created",
                ),
            )
        return run, created


def transition_run(
    session: Session,
    run: ResearchRun,
    target: RunStatus,
    *,
    event_type: RunEventType,
    message: str | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
    error_summary: str | None = None,
    event_stage: RunStatus | None = None,
):
    ensure_run_transition(run.status, target)
    now = datetime.now(tz=UTC)

    if target not in {
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    } and run.started_at is None:
        run.started_at = now
    if target in TERMINAL_RUN_STATUSES:
        run.finished_at = now
    if error_summary is not None:
        run.error_summary = error_summary[:_ERROR_SUMMARY_LIMIT]

    run.status = target
    return RunEventRepository(session).append(
        run.project_id,
        run.id,
        RunEventCreate(
            event_type=event_type,
            stage=event_stage or target,
            message=message,
            payload=payload or {},
            dedupe_key=dedupe_key,
        ),
    )


def finalize_cancelled_run(session: Session, run: ResearchRun) -> None:
    if run.cancel_requested_at is None:
        run.cancel_requested_at = datetime.now(tz=UTC)
    if run.status is RunStatus.CANCELLED:
        return
    transition_run(
        session,
        run,
        RunStatus.CANCELLED,
        event_type=RunEventType.RUN_CANCELLED,
        message="Research run cancelled",
        dedupe_key="run:cancelled",
    )


def request_run_cancel(
    database: Database,
    run_id: UUID,
    project_id: UUID | None = None,
) -> CancelOutcome:
    with database.session_scope() as session:
        run = lock_run(session, run_id, project_id)
        if run is None:
            raise EntityNotFound("Research run not found")

        if run.status is RunStatus.CANCELLED:
            return CancelOutcome(run=run, finalized=True)
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunAlreadyFinished(
                f"Run is already finished with status {run.status.value}"
            )

        if run.cancel_requested_at is None:
            run.cancel_requested_at = datetime.now(tz=UTC)
            RunEventRepository(session).append(
                run.project_id,
                run.id,
                RunEventCreate(
                    event_type=RunEventType.CANCEL_REQUESTED,
                    stage=run.status,
                    message="Cancellation requested",
                    dedupe_key="run:cancel-requested",
                ),
            )

        finalized = run.status in {
            RunStatus.QUEUED,
            RunStatus.AWAITING_APPROVAL,
        }
        if finalized:
            finalize_cancelled_run(session, run)

        session.flush()
        session.refresh(run)
        return CancelOutcome(run=run, finalized=finalized)


def fail_run(
    database: Database,
    run_id: UUID,
    project_id: UUID,
    error_message: str,
    *,
    error_type: str | None = None,
) -> bool:
    with database.session_scope() as session:
        run = lock_run(session, run_id, project_id)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return False

        failed_stage = run.status
        payload = {"error_type": error_type} if error_type else {}
        transition_run(
            session,
            run,
            RunStatus.FAILED,
            event_type=RunEventType.PHASE_FAILED,
            event_stage=failed_stage,
            message=f"Research run failed: {error_message}",
            payload=payload,
            dedupe_key="run:failed",
            error_summary=error_message,
        )
        return True


def record_retry(
    database: Database,
    run_id: UUID,
    project_id: UUID,
    *,
    attempt: int,
    max_retries: int,
    error_type: str,
) -> bool:
    with database.session_scope() as session:
        run = lock_run(session, run_id, project_id)
        if (
            run is None
            or run.status in TERMINAL_RUN_STATUSES
            or run.cancel_requested_at is not None
        ):
            return False

        RunEventRepository(session).append(
            run.project_id,
            run.id,
            RunEventCreate(
                event_type=RunEventType.RETRY_SCHEDULED,
                stage=run.status,
                message=f"Transient error; retry {attempt} of {max_retries} scheduled",
                payload={
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "error_type": error_type,
                },
                dedupe_key=f"run:retry:{attempt}",
            ),
        )
        return True
