import hashlib
import os
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
POLL_INTERVAL_SECONDS = 0.5
POLL_TIMEOUT_SECONDS = 30


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as http_client:
        yield http_client


def _wait_for_json(client, path, predicate):
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_payload = None
    while time.monotonic() < deadline:
        response = client.get(path)
        if response.status_code == 200:
            last_payload = response.json()
            if predicate(last_payload):
                return last_payload
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"Timed out waiting for {path}; last payload: {last_payload}")


def test_health_and_readiness(client):
    health = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "api"
    assert health.headers["access-control-allow-origin"] == "http://localhost:3000"

    v1_health = client.get("/api/v1/health")
    assert v1_health.status_code == 200
    assert v1_health.json()["status"] == "ok"

    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "ready"
    assert {key for key, check in payload["checks"].items() if check["status"] == "ok"} == {
        "database",
        "schema",
        "redis",
        "storage",
    }


def test_storage_roundtrip_between_api_and_worker(client):
    content = f"phase-1 roundtrip {uuid.uuid4()}\n".encode()
    expected_sha = hashlib.sha256(content).hexdigest()

    upload = client.post(
        "/api/v1/dev/storage-smoke",
        files={"file": ("phase1-smoke.txt", content, "text/plain")},
    )
    assert upload.status_code == 202, upload.text
    uploaded = upload.json()
    assert uploaded["sha256"] == expected_sha
    assert uploaded["size_bytes"] == len(content)

    result = _wait_for_json(
        client,
        f"/api/v1/dev/storage-smoke/{uploaded['file_id']}",
        lambda payload: payload.get("status") == "completed",
    )
    assert result["sha256"] == expected_sha
    assert result["size_bytes"] == len(content)
    assert result["file_id"] == uploaded["file_id"]
    assert result["worker"]

    download = client.get(f"/api/v1/dev/storage-smoke/{uploaded['file_id']}/original")
    assert download.status_code == 200
    assert download.content == content

    deleted = client.delete(f"/api/v1/dev/storage-smoke/{uploaded['file_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_original"] is True
    assert deleted.json()["deleted_result"] is True

    missing = client.get(f"/api/v1/dev/storage-smoke/{uploaded['file_id']}/original")
    assert missing.status_code == 404


def test_celery_ping_task(client):
    dispatch = client.post("/api/v1/dev/ping")
    assert dispatch.status_code == 202, dispatch.text
    task_id = dispatch.json()["task_id"]

    task = _wait_for_json(
        client,
        f"/api/v1/dev/tasks/{task_id}",
        lambda payload: payload.get("state") == "SUCCESS",
    )
    assert task["result"]["pong"] is True
    assert task["result"]["worker"]
