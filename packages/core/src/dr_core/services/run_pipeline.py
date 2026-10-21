"""Phase 3 planning scaffold; the LangGraph workflow replaces it in Phase 5."""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from dr_core.config import get_settings
from dr_core.db import Database, get_database
from dr_core.db.enums import TERMINAL_RUN_STATUSES, RunEventType, RunStatus
from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.db.repositories import (
    ResearchQuestionRepository,
    RunEventRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.schemas import ResearchQuestionCreate, RunEventCreate
from dr_core.services.runs import (
    finalize_cancelled_run,
    lock_run,
    transition_run,
)


class TransientRunError(Exception):
    """A retryable failure such as a temporary upstream outage."""


class PermanentRunError(Exception):
    """A non-retryable failure such as invalid persisted input."""


@dataclass(frozen=True, slots=True)
class RunPipelineResult:
    run_id: UUID
    status: RunStatus
    paused_for_approval: bool = False


def _result(run: ResearchRun) -> RunPipelineResult:
    return RunPipelineResult(
        run_id=run.id,
        status=run.status,
        paused_for_approval=run.status is RunStatus.AWAITING_APPROVAL,
    )


def _ensure_not_cancelled(session, run: ResearchRun) -> bool:
    if run.cancel_requested_at is not None or run.status is RunStatus.CANCELLED:
        finalize_cancelled_run(session, run)
        return True
    return False


def execute_run_pipeline(
    database: Database | None = None,
    run_id: UUID | None = None,
    project_id: UUID | None = None,
    *,
    simulate_failure: bool = False,
    pause_seconds: float | None = None,
) -> RunPipelineResult:
    if run_id is None or project_id is None:
        raise ValueError("run_id and project_id are required")

    database = database or get_database()
    pause = (
        get_settings().run_phase_pause_seconds
        if pause_seconds is None
        else pause_seconds
    )

    with database.session_scope() as session:
        run = lock_run(session, run_id, project_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        if run.status in TERMINAL_RUN_STATUSES:
            return _result(run)
        if simulate_failure:
            raise PermanentRunError(
                "Simulated task failure requested by development tooling"
            )
        if _ensure_not_cancelled(session, run):
            return _result(run)
        if run.status is RunStatus.AWAITING_APPROVAL:
            return _result(run)
        if run.status is RunStatus.QUEUED:
            transition_run(
                session,
                run,
                RunStatus.PLANNING,
                event_type=RunEventType.PHASE_STARTED,
                message="Research planning started",
                dedupe_key="phase:planning:started",
            )
        elif run.status is not RunStatus.PLANNING:
            return _result(run)

    if pause > 0:
        time.sleep(pause)

    with database.session_scope() as session:
        run = lock_run(session, run_id, project_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        if run.status in TERMINAL_RUN_STATUSES:
            return _result(run)
        if simulate_failure:
            raise PermanentRunError(
                "Simulated task failure requested by development tooling"
            )
        if _ensure_not_cancelled(session, run):
            return _result(run)
        if run.status is RunStatus.AWAITING_APPROVAL:
            return _result(run)
        if run.status is not RunStatus.PLANNING:
            return _result(run)

        project = session.get(ResearchProject, project_id)
        if project is None or project.deleted_at is not None:
            raise PermanentRunError("Research project is missing or deleted")

        question = ResearchQuestionRepository(session).create(
            project.id,
            run.id,
            ResearchQuestionCreate(
                question=project.topic.strip(),
                priority=10,
            ),
        )

        RunEventRepository(session).append(
            run.project_id,
            run.id,
            RunEventCreate(
                event_type=RunEventType.PHASE_COMPLETED,
                stage=RunStatus.PLANNING,
                message="Research plan prepared",
                payload={"question_ids": [str(question.id)]},
                dedupe_key="phase:planning:completed",
            ),
        )
        transition_run(
            session,
            run,
            RunStatus.AWAITING_APPROVAL,
            event_type=RunEventType.AWAITING_APPROVAL,
            message="Research plan ready for approval",
            payload={"question_id": str(question.id)},
            dedupe_key="run:awaiting-approval",
        )
        return _result(run)
