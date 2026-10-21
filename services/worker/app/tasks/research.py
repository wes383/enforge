import logging
from uuid import UUID

from app.celery_app import celery_app

from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.research.graph import run_research_workflow
from dr_core.services import (
    PermanentRunError,
    TransientRunError,
    fail_run,
    record_retry,
)
from dr_core.services.approvals import resume_payload
from dr_core.tasks.names import EXECUTE_RESEARCH_RUN_TASK
from dr_core.tasks.retry import retry_countdown_seconds

logger = logging.getLogger(__name__)

_settings = get_settings()

if (
    _settings.llm_workflow_enabled
    and _settings.effective_run_task_soft_time_limit_seconds
    > _settings.run_task_soft_time_limit_seconds
):
    logger.warning(
        "Model writing is enabled; raising the run time limit from %ss to %ss "
        "(hard %ss). Tune RUN_TASK_SOFT_TIME_LIMIT_SECONDS, "
        "LLM_REQUEST_TIMEOUT_SECONDS or RESEARCH_CLAIM_LLM_REVIEW_LIMIT to change it.",
        _settings.run_task_soft_time_limit_seconds,
        _settings.effective_run_task_soft_time_limit_seconds,
        _settings.effective_run_task_hard_time_limit_seconds,
    )


def _error_message(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:4000]


def _safe_fail(run_id: UUID, project_id: UUID, exc: BaseException) -> None:
    try:
        fail_run(
            get_database(),
            run_id,
            project_id,
            _error_message(exc),
            error_type=type(exc).__name__,
        )
    except Exception:
        logger.exception("Unable to persist failure state for run %s", run_id)


@celery_app.task(
    bind=True,
    name=EXECUTE_RESEARCH_RUN_TASK,
    max_retries=_settings.run_task_max_retries,
    soft_time_limit=_settings.effective_run_task_soft_time_limit_seconds,
    time_limit=_settings.effective_run_task_hard_time_limit_seconds,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_research_run(
    self,
    run_id: str,
    project_id: str,
    simulate_failure: bool = False,
    approval_id: str | None = None,
) -> dict[str, object]:
    run_uuid = UUID(run_id)
    project_uuid = UUID(project_id)
    database = get_database()

    try:
        if simulate_failure:
            raise PermanentRunError(
                "Simulated task failure requested by development tooling"
            )
        resume = (
            resume_payload(database, run_uuid, UUID(approval_id))
            if approval_id
            else None
        )
        result = run_research_workflow(
            database,
            run_uuid,
            project_uuid,
            resume=resume,
        )
        return {
            "run_id": str(result.run_id),
            "status": result.status.value,
            "paused_for_approval": result.paused_for_approval,
            "approval_id": (
                str(result.approval_id) if result.approval_id else None
            ),
        }
    except EntityNotFound:
        logger.warning("Discarding task for missing run %s", run_id)
        return {"run_id": run_id, "status": "ignored"}
    except TransientRunError as exc:
        max_retries = self.max_retries or 0
        if self.request.retries >= max_retries:
            _safe_fail(run_uuid, project_uuid, exc)
            raise
        attempt = self.request.retries + 1
        try:
            record_retry(
                database,
                run_uuid,
                project_uuid,
                attempt=attempt,
                max_retries=max_retries,
                error_type=type(exc).__name__,
            )
        except Exception:
            logger.exception("Unable to record retry for run %s", run_id)
        raise self.retry(exc=exc, countdown=retry_countdown_seconds(attempt))
    except PermanentRunError as exc:
        _safe_fail(run_uuid, project_uuid, exc)
        raise
    except Exception as exc:
        _safe_fail(run_uuid, project_uuid, exc)
        raise
