from uuid import UUID

from dr_core.config import get_settings
from dr_core.tasks import get_celery_client
from dr_core.tasks.names import EXECUTE_RESEARCH_RUN_TASK


def dispatch_research_run(run_id: UUID, project_id: UUID) -> None:
    get_celery_client().send_task(
        EXECUTE_RESEARCH_RUN_TASK,
        args=[str(run_id), str(project_id)],
        task_id=str(run_id),
        queue=get_settings().celery_task_default_queue,
    )


def revoke_research_run(run_id: UUID) -> None:
    get_celery_client().control.revoke(str(run_id), terminate=False)


def dispatch_simulated_failure(run_id: UUID, project_id: UUID) -> str:
    result = get_celery_client().send_task(
        EXECUTE_RESEARCH_RUN_TASK,
        args=[str(run_id), str(project_id), True],
        queue=get_settings().celery_task_default_queue,
    )
    return str(result.id)
