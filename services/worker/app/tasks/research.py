import logging
from uuid import UUID

from app.celery_app import celery_app

from dr_core.config import get_settings
from dr_core.db import get_database
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.services import (
    PermanentRunError,
    TransientRunError,
    execute_run_pipeline,
    fail_run,
    record_retry,
)
from dr_core.tasks.names import EXECUTE_RESEARCH_RUN_TASK
from dr_core.tasks.retry import retry_countdown_seconds

logger = logging.getLogger(__name__)

_settings = get_settings()


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
    soft_time_limit=_settings.run_task_soft_time_limit_seconds,
    time_limit=_settings.run_task_hard_time_limit_seconds,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_research_run(
    self,
    run_id: str,
    project_id: str,
    simulate_failure: bool = False,
) -> dict[str, str]:
    run_uuid = UUID(run_id)
    project_uuid = UUID(project_id)
    database = get_database()

    try:
        result = execute_run_pipeline(
            database,
            run_uuid,
            project_uuid,
            simulate_failure=simulate_failure,
        )
        return {
            "run_id": str(result.run_id),
            "status": result.status.value,
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
