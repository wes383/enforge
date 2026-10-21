import json
import os
import time
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, func, select, text

from dr_core.config import get_settings
from dr_core.db.models import (
    ResearchProject,
    ResearchQuestion,
    ResearchRun,
    RunEvent,
    SourceVersion,
)
from dr_core.db.repositories import RunRepository
from dr_core.schemas import RunCreate
from dr_core.services import record_retry
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage
from dr_core.tasks import EXECUTE_RESEARCH_RUN_TASK, get_celery_client

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API = "/api/v1"
POLL_INTERVAL_SECONDS = 0.05
POLL_TIMEOUT_SECONDS = 30


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as http_client:
        yield http_client


def _cleanup_projects(database, project_ids):
    if not project_ids:
        return
    storage = get_file_storage()
    with database.session_scope() as session:
        thread_ids = list(
            session.scalars(
                select(ResearchRun.workflow_thread_id).where(
                    ResearchRun.project_id.in_(project_ids)
                )
            )
        )
        storage_keys = list(
            session.scalars(
                select(SourceVersion.storage_key).where(
                    SourceVersion.project_id.in_(project_ids),
                    SourceVersion.storage_key.is_not(None),
                )
            )
        )
        session.execute(
            delete(ResearchProject).where(ResearchProject.id.in_(project_ids))
        )
        for thread_id in thread_ids:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                exists = session.scalar(
                    text("SELECT to_regclass(:name)"),
                    {"name": table},
                )
                if exists:
                    session.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                        {"thread_id": thread_id},
                    )
    for storage_key in storage_keys:
        try:
            storage.delete(storage_key)
        except (StorageFileNotFound, StorageError):
            pass


@pytest.fixture
def project_factory(client, database):
    created: list[UUID] = []

    def factory(**overrides):
        payload = {
            "title": f"Phase 3 API {uuid.uuid4().hex[:8]}",
            "topic": "异步研究任务编排、事件流与取消语义",
            "language": "zh",
        }
        payload.update(overrides)
        response = client.post(f"{API}/projects", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        created.append(UUID(body["id"]))
        return body

    yield factory
    _cleanup_projects(database, created)


def _create_run(client, project_id, *, idempotency_key=None):
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = client.post(
        f"{API}/projects/{project_id}/runs",
        json={},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


def _wait_for_run(client, run_id, statuses, timeout=POLL_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"{API}/runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in statuses:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach {statuses}; last: {last}")


def _wait_for_task(client, task_id, timeout=POLL_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"{API}/dev/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] in {"SUCCESS", "FAILURE"}:
            return last
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish; last: {last}")


def _events(client, run_id, **params):
    response = client.get(f"{API}/runs/{run_id}/events", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _collect_sse(
    client,
    run_id,
    *,
    headers=None,
    after=0,
    stop=None,
    read_timeout=1.5,
):
    events = []
    pending_id = None
    closed = False
    try:
        with client.stream(
            "GET",
            f"{API}/runs/{run_id}/events/stream",
            params={"after": after},
            headers=headers or {},
            timeout=httpx.Timeout(30.0, read=read_timeout),
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("id: "):
                    pending_id = int(line[4:])
                elif line.startswith("data: "):
                    event = json.loads(line[6:])
                    if pending_id is not None:
                        assert event["id"] == pending_id
                    pending_id = None
                    events.append(event)
                    if stop is not None and stop(events):
                        return events, False
        closed = True
    except httpx.ReadTimeout:
        pass
    return events, closed


def test_project_crud_pagination_and_soft_delete(client, project_factory):
    first = project_factory(title="Phase 3 project A", topic="Topic A")
    second = project_factory(title="Phase 3 project B", topic="Topic B")

    listing = client.get(f"{API}/projects", params={"limit": 200})
    assert listing.status_code == 200
    listed = listing.json()
    assert listed["total"] >= 2
    assert {first["id"], second["id"]} <= {item["id"] for item in listed["items"]}

    page_one = client.get(f"{API}/projects", params={"limit": 1, "offset": 0})
    page_two = client.get(f"{API}/projects", params={"limit": 1, "offset": 1})
    assert page_one.status_code == page_two.status_code == 200
    assert len(page_one.json()["items"]) == 1
    assert len(page_two.json()["items"]) == 1
    assert page_one.json()["items"][0]["id"] != page_two.json()["items"][0]["id"]
    assert page_one.json()["total"] == listed["total"]
    assert page_one.json()["limit"] == 1

    detail = client.get(f"{API}/projects/{first['id']}")
    assert detail.status_code == 200
    assert detail.json()["topic"] == "Topic A"

    patched = client.patch(
        f"{API}/projects/{first['id']}",
        json={"title": "Phase 3 project A renamed", "research_scope": "API scope"},
    )
    assert patched.status_code == 200, patched.text
    patched_body = patched.json()
    assert patched_body["title"] == "Phase 3 project A renamed"
    assert patched_body["research_scope"] == "API scope"
    assert patched_body["topic"] == "Topic A"
    assert patched_body["budget_usd"] is None
    assert patched_body["updated_at"] >= detail.json()["updated_at"]

    deleted = client.delete(f"{API}/projects/{first['id']}")
    assert deleted.status_code == 204
    assert client.get(f"{API}/projects/{first['id']}").status_code == 404
    assert client.delete(f"{API}/projects/{first['id']}").status_code == 404
    assert client.post(f"{API}/projects/{first['id']}/runs", json={}).status_code == 404

    after_delete = client.get(f"{API}/projects", params={"limit": 200}).json()
    assert first["id"] not in {item["id"] for item in after_delete["items"]}


def test_run_lifecycle_events_sse_and_idempotency(client, project_factory, database):
    project = project_factory()
    idempotency_key = f"phase3-{uuid.uuid4()}"
    run = _create_run(client, project["id"], idempotency_key=idempotency_key)
    assert run["status"] == "queued"
    assert run["started_at"] is None
    assert run["finished_at"] is None

    duplicate = client.post(
        f"{API}/projects/{project['id']}/runs",
        json={},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == run["id"]
    assert duplicate.json()["created_at"] == run["created_at"]

    settled = _wait_for_run(client, run["id"], {"awaiting_approval", "failed"})
    assert settled["status"] == "awaiting_approval", settled
    assert settled["started_at"] is not None
    assert settled["finished_at"] is None
    assert settled["error_summary"] is None

    events = _events(client, run["id"])
    event_ids = [event["id"] for event in events]
    assert event_ids == sorted(set(event_ids))
    types = [event["event_type"] for event in events]
    assert types.count("run_created") == 1
    assert types.count("phase_started") == 1
    assert types.count("phase_completed") == 1
    assert types.count("awaiting_approval") == 1

    run_uuid = UUID(run["id"])
    with database.session_scope() as session:
        question_count = session.scalar(
            select(func.count())
            .select_from(ResearchQuestion)
            .where(ResearchQuestion.run_id == run_uuid)
        )
        initial_event_count = session.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id == run_uuid)
        )
    assert question_count >= 1
    assert initial_event_count == len(events)

    duplicate_task = get_celery_client().send_task(
        EXECUTE_RESEARCH_RUN_TASK,
        args=[run["id"], project["id"]],
        queue=get_settings().celery_task_default_queue,
    )
    finished_task = _wait_for_task(client, duplicate_task.id)
    assert finished_task["state"] == "SUCCESS"
    assert finished_task["result"]["status"] == "awaiting_approval"

    repeated_events = _events(client, run["id"])
    assert [event["id"] for event in repeated_events] == event_ids
    with database.session_scope() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchQuestion)
                .where(ResearchQuestion.run_id == run_uuid)
            )
            >= 1
        )

    cursor = event_ids[-2]
    replayed, _ = _collect_sse(
        client,
        run["id"],
        headers={"Last-Event-ID": str(cursor)},
        stop=lambda received: len(received) >= 1,
    )
    assert [event["id"] for event in replayed] == [event_ids[-1]]

    replayed_query, _ = _collect_sse(
        client,
        run["id"],
        after=event_ids[-3],
        stop=lambda received: len(received) >= 2,
    )
    assert [event["id"] for event in replayed_query] == event_ids[-2:]

    no_duplicates, closed = _collect_sse(
        client,
        run["id"],
        headers={"Last-Event-ID": str(event_ids[-1])},
        read_timeout=1.5,
    )
    assert no_duplicates == []
    assert closed is False

    invalid_cursor = client.get(
        f"{API}/runs/{run['id']}/events/stream",
        headers={"Last-Event-ID": "not-an-integer"},
    )
    assert invalid_cursor.status_code == 400

    cancelled = client.post(f"{API}/runs/{run['id']}/cancel")
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested_at"] is not None
    assert cancelled.json()["finished_at"] is not None

    final_events = _events(client, run["id"])
    assert [event["event_type"] for event in final_events[-2:]] == [
        "cancel_requested",
        "run_cancelled",
    ]
    final_count = len(final_events)

    cancel_again = client.post(f"{API}/runs/{run['id']}/cancel")
    assert cancel_again.status_code == 202
    assert cancel_again.json()["status"] == "cancelled"
    time.sleep(1.0)
    assert len(_events(client, run["id"])) == final_count

    streamed, closed = _collect_sse(client, run["id"], read_timeout=5.0)
    assert closed is True
    assert [event["id"] for event in streamed] == [
        event["id"] for event in final_events
    ]
    assert streamed[-1]["event_type"] == "run_cancelled"

    assert client.delete(f"{API}/projects/{project['id']}").status_code == 204
    assert client.get(f"{API}/runs/{run['id']}").status_code == 404
    assert client.get(f"{API}/runs/{run['id']}/events").status_code == 404


def test_cooperative_cancellation_stops_active_pipeline(
    client,
    project_factory,
    database,
):
    project = project_factory()
    run = _create_run(client, project["id"])

    deadline = time.monotonic() + 10.0
    observed = None
    while time.monotonic() < deadline:
        current = client.get(f"{API}/runs/{run['id']}").json()
        if current["status"] in {"planning", "awaiting_approval", "failed"}:
            observed = current["status"]
            break
        time.sleep(0.02)
    assert observed is not None

    cancel = client.post(f"{API}/runs/{run['id']}/cancel")
    assert cancel.status_code == 202, cancel.text
    cancel_status = cancel.json()["status"]
    assert cancel_status in {"planning", "cancelled"}

    final = _wait_for_run(client, run["id"], {"cancelled", "failed"})
    assert final["status"] == "cancelled"
    assert final["cancel_requested_at"] is not None
    assert final["finished_at"] is not None

    events = _events(client, run["id"])
    types = [event["event_type"] for event in events]
    assert "cancel_requested" in types
    assert "run_cancelled" in types
    assert types.count("run_cancelled") == 1

    # The in-flight node may finish, but no later node may start after the
    # cancellation is observed.
    assert types[-1] == "run_cancelled"
    assert "awaiting_approval" not in types


def test_simulated_failure_is_terminal_and_readable(client, project_factory):
    project = project_factory()
    run = _create_run(client, project["id"])

    dispatch = client.post(f"{API}/dev/runs/{run['id']}/fail")
    assert dispatch.status_code == 202, dispatch.text
    assert dispatch.json()["task_id"]

    failed = _wait_for_run(client, run["id"], {"failed"})
    assert failed["status"] == "failed", failed
    assert failed["error_summary"]
    assert "Simulated task failure" in failed["error_summary"]
    assert failed["finished_at"] is not None

    events = _events(client, run["id"])
    failed_events = [
        event for event in events if event["event_type"] == "phase_failed"
    ]
    assert len(failed_events) == 1
    assert failed_events[0]["payload"]["error_type"] == "PermanentRunError"

    streamed, closed = _collect_sse(client, run["id"], read_timeout=5.0)
    assert closed is True
    assert streamed[-1]["event_type"] == "phase_failed"
    assert streamed[-1]["id"] == events[-1]["id"]

    cancel = client.post(f"{API}/runs/{run['id']}/cancel")
    assert cancel.status_code == 409


def test_retry_events_are_deduplicated_and_queued_cancel_is_immediate(
    client,
    project_factory,
    database,
):
    project = project_factory()
    with database.session_scope() as session:
        queued = RunRepository(session).create(
            UUID(project["id"]),
            RunCreate(idempotency_key=f"retry-{uuid.uuid4()}"),
        )
        run_id, project_id = queued.id, queued.project_id

    assert record_retry(
        database,
        run_id,
        project_id,
        attempt=1,
        max_retries=3,
        error_type="TransientRunError",
    )
    assert record_retry(
        database,
        run_id,
        project_id,
        attempt=1,
        max_retries=3,
        error_type="TransientRunError",
    )
    assert record_retry(
        database,
        run_id,
        project_id,
        attempt=2,
        max_retries=3,
        error_type="TransientRunError",
    )

    events = _events(client, run_id)
    retries = [
        event for event in events if event["event_type"] == "retry_scheduled"
    ]
    assert [event["payload"]["attempt"] for event in retries] == [1, 2]
    assert retries[0]["payload"]["max_retries"] == 3
    assert retries[0]["payload"]["error_type"] == "TransientRunError"

    cancel = client.post(f"{API}/runs/{run_id}/cancel")
    assert cancel.status_code == 202, cancel.text
    assert cancel.json()["status"] == "cancelled"
    assert cancel.json()["finished_at"] is not None

    event_types = [
        event["event_type"] for event in _events(client, run_id)
    ]
    assert event_types.count("cancel_requested") == 1
    assert event_types.count("run_cancelled") == 1
