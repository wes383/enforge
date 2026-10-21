import hashlib
import json
import logging
import socket
from datetime import UTC, datetime

from app.celery_app import celery_app
from dr_core.config import get_settings
from dr_core.storage import (
    LocalFileStorage,
    StorageError,
    StorageFileNotFound,
    smoke_original_key,
    smoke_result_key,
)
from dr_core.storage.smoke_keys import normalize_file_id
from dr_core.tasks.names import STORAGE_SMOKE_TASK

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name=STORAGE_SMOKE_TASK, max_retries=3)
def storage_smoke(self, file_id: str, expected_sha256: str) -> dict[str, object]:
    try:
        normalized_id = normalize_file_id(file_id)
        storage = LocalFileStorage(get_settings().file_storage_root)
        with storage.open(smoke_original_key(normalized_id)) as handle:
            content = handle.read()
    except (StorageFileNotFound, StorageError) as exc:
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 10)) from exc

    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("Checksum mismatch while reading the shared file volume")

    result: dict[str, object] = {
        "file_id": normalized_id,
        "status": "completed",
        "sha256": actual_sha256,
        "size_bytes": len(content),
        "worker": socket.gethostname(),
        "completed_at": datetime.now(tz=UTC).isoformat(),
    }
    storage.save(
        smoke_result_key(normalized_id),
        json.dumps(result, ensure_ascii=False).encode("utf-8"),
    )
    logger.info("Storage smoke task completed for file %s", normalized_id)
    return result
