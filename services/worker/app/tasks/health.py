import logging
import socket
from datetime import UTC, datetime

from app.celery_app import celery_app
from dr_core.tasks.names import DEBUG_PING_TASK

logger = logging.getLogger(__name__)


@celery_app.task(name=DEBUG_PING_TASK)
def ping() -> dict[str, object]:
    payload: dict[str, object] = {
        "pong": True,
        "worker": socket.gethostname(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }
    logger.info("Ping task completed on %s", payload["worker"])
    return payload
