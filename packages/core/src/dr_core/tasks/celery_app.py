from functools import lru_cache

from celery import Celery

from dr_core.config import get_settings
from dr_core.observability import install_log_redaction


def create_celery_app(include: list[str] | None = None) -> Celery:
    settings = get_settings()
    install_log_redaction()
    app = Celery(
        "dr_worker",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=include or [],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        task_default_queue=settings.celery_task_default_queue,
        task_routes={
            "research.execute_run": {
                "queue": settings.celery_task_default_queue,
            },
            "documents.parse": {"queue": settings.celery_parse_queue},
        },
        result_expires=86400,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 43200},
        task_soft_time_limit=settings.run_task_soft_time_limit_seconds,
        task_time_limit=settings.run_task_hard_time_limit_seconds,
    )
    return app


@lru_cache
def get_celery_client() -> Celery:
    """A producer-only client; tasks are registered by the worker process."""
    return create_celery_app()
