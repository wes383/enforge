import os
import re
import time
import uuid
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import delete, func, select, text

from dr_core.config import get_settings
from dr_core.db.enums import (
    ArtifactType,
    DraftSectionStatus,
    SourceVersionKind,
)
from dr_core.db.models import (
    Artifact,
    CitationAudit,
    DraftSection,
    EvidenceItem,
    ResearchProject,
    ResearchQuestion,
    ResearchRun,
    SearchQuery,
    SourceVersion,
)
from dr_core.db.repositories import RunRepository
from dr_core.schemas import RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage
from dr_core.tasks import EXECUTE_RESEARCH_RUN_TASK, get_celery_client

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API = "/api/v1"
POLL_INTERVAL_SECONDS = 0.1
POLL_TIMEOUT_SECONDS = 45


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as http_client:
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
        storage_keys.extend(
            session.scalars(
                select(Artifact.storage_key).where(
                    Artifact.project_id.in_(project_ids)
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
            "title": f"Phase 5 workflow {uuid.uuid4().hex[:8]}",
            "topic": "AI regulation and evidence quality",
            "language": "en",
        }
        payload.update(overrides)
        response = client.post(f"{API}/projects", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        created.append(UUID(body["id"]))
        return body

    yield factory
    _cleanup_projects(database, created)


def _create_run(client, project_id):
    response = client.post(f"{API}/projects/{project_id}/runs", json={})
    assert response.status_code == 202, response.text
    return response.json()


def _get_run(client, run_id):
    response = client.get(f"{API}/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_run(client, run_id, statuses, timeout=POLL_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _get_run(client, run_id)
        if last["status"] in statuses:
            return last
        if last["status"] == "failed":
            raise AssertionError(f"run failed: {last}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach {statuses}; last: {last}")


def _approvals(client, run_id, *, status=None):
    params = {"status": status} if status else {}
    response = client.get(f"{API}/runs/{run_id}/approvals", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _wait_for_approval(
    client,
    run_id,
    kind,
    *,
    version=None,
    timeout=POLL_TIMEOUT_SECONDS,
):
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        last = _approvals(client, run_id, status="pending")
        for approval in last:
            if approval["kind"] == kind and (
                version is None or approval["version"] == version
            ):
                return approval
        run = _get_run(client, run_id)
        if run["status"] == "failed":
            raise AssertionError(f"run failed: {run}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"approval {kind} did not appear for run {run_id}; pending: {last}"
    )


def _decide(client, run_id, approval_id, payload):
    return client.post(
        f"{API}/runs/{run_id}/approvals/{approval_id}",
        json=payload,
    )


def _events(client, run_id):
    response = client.get(f"{API}/runs/{run_id}/events", params={"limit": 1000})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _thread_id(database, run_id):
    with database.session() as session:
        return session.scalar(
            select(ResearchRun.workflow_thread_id).where(
                ResearchRun.id == UUID(str(run_id))
            )
        )


def test_research_workflow_end_to_end_with_approvals_and_checkpoints(
    client,
    database,
    project_factory,
):
    project = project_factory(research_depth="standard", title="Phase 5 workflow test")
    run = _create_run(client, project["id"])
    plan = _wait_for_approval(client, run["id"], "plan")
    assert plan["version"] == 1
    assert len(plan["payload"]["questions"]) >= 3

    paused = _get_run(client, run["id"])
    assert paused["status"] == "awaiting_approval"
    assert paused["checkpoint_id"]

    thread_id = _thread_id(database, run["id"])
    assert thread_id
    with PostgresSaver.from_conn_string(
        os.environ["DATABASE_URL"]
    ) as checkpointer:
        saved = checkpointer.get_tuple(
            {"configurable": {"thread_id": thread_id}}
        )
    assert saved is not None
    assert saved.checkpoint["channel_values"]["plan"]["version"] == 1
    first_checkpoint = paused["checkpoint_id"]

    approved = _decide(
        client,
        run["id"],
        plan["id"],
        {"decision": "approved"},
    )
    assert approved.status_code == 202, approved.text
    assert approved.json()["status"] == "approved"

    outline = _wait_for_approval(client, run["id"], "outline", timeout=90)
    paused_again = _get_run(client, run["id"])
    assert paused_again["status"] == "awaiting_approval"
    assert paused_again["checkpoint_id"]
    assert paused_again["checkpoint_id"] != first_checkpoint

    with database.session() as session:
        question_count = session.scalar(
            select(func.count())
            .select_from(ResearchQuestion)
            .where(ResearchQuestion.run_id == UUID(run["id"]))
        )
        evidence_count = session.scalar(
            select(func.count())
            .select_from(EvidenceItem)
            .where(EvidenceItem.run_id == UUID(run["id"]))
        )
        full_text_count = session.scalar(
            select(func.count())
            .select_from(SourceVersion)
            .where(
                SourceVersion.project_id == UUID(project["id"]),
                SourceVersion.content_kind == SourceVersionKind.FULL_TEXT,
            )
        )
        max_round = session.scalar(
            select(func.max(SearchQuery.round_index)).where(
                SearchQuery.run_id == UUID(run["id"])
            )
        )
    assert question_count >= 3
    assert evidence_count > 0
    assert full_text_count > 0
    assert max_round and max_round >= 2

    event_types = [event["event_type"] for event in _events(client, run["id"])]
    assert event_types.count("awaiting_approval") >= 2
    assert "source_fetched" in event_types
    assert "source_parsed" in event_types
    assert "evidence_extracted" in event_types

    outline_approved = _decide(
        client,
        run["id"],
        outline["id"],
        {"decision": "approved"},
    )
    assert outline_approved.status_code == 202, outline_approved.text

    completed = _wait_for_run(
        client,
        run["id"],
        {"completed", "failed"},
        timeout=90,
    )
    assert completed["status"] == "completed", completed
    assert completed["finished_at"]
    assert completed["error_summary"] is None

    with database.session() as session:
        artifacts = list(
            session.scalars(
                select(Artifact).where(Artifact.run_id == UUID(run["id"]))
            )
        )
        sections = list(
            session.scalars(
                select(DraftSection).where(
                    DraftSection.run_id == UUID(run["id"])
                )
            )
        )
        audits = list(
            session.scalars(
                select(CitationAudit).where(
                    CitationAudit.run_id == UUID(run["id"])
                )
            )
        )
    assert {item.artifact_type.value for item in artifacts} == {
        "research_report",
        "reference_list",
        "markdown_export",
    }
    assert sections
    assert all(
        item.generation_status is DraftSectionStatus.VERIFIED for item in sections
    )
    assert audits
    assert not any(item.status.value == "failed" for item in audits)

    report = next(
        item for item in artifacts if item.artifact_type.value == "research_report"
    )
    content = get_file_storage().read(report.storage_key).decode("utf-8")
    assert "Phase 5 workflow test" in content
    assert "## References" in content
    body, reference_block = content.split("## References", 1)
    cited = set(re.findall(r"\[S(\d+)\]", body))
    listed = set(re.findall(r"\[S(\d+)\]", reference_block))
    assert cited
    assert cited <= listed

    duplicate = _decide(
        client,
        run["id"],
        plan["id"],
        {"decision": "approved"},
    )
    assert duplicate.status_code == 409


def test_plan_rejection_modification_and_cancellation(
    client,
    database,
    project_factory,
):
    project = project_factory(research_depth="quick", title="Phase 5 revision test")
    run = _create_run(client, project["id"])
    first = _wait_for_approval(client, run["id"], "plan")

    missing_content = _decide(
        client,
        run["id"],
        first["id"],
        {"decision": "modified"},
    )
    assert missing_content.status_code == 422

    invalid_content = _decide(
        client,
        run["id"],
        first["id"],
        {"decision": "modified", "content": {"questions": []}},
    )
    assert invalid_content.status_code == 422

    rejected = _decide(
        client,
        run["id"],
        first["id"],
        {
            "decision": "rejected",
            "feedback": "Add a question about enforcement costs.",
        },
    )
    assert rejected.status_code == 202, rejected.text
    second = _wait_for_approval(client, run["id"], "plan", version=2, timeout=60)
    assert second["payload"]["summary"]
    assert any(
        "enforcement costs" in item["question"].casefold()
        for item in second["payload"]["questions"]
    )

    edited_plan = {
        "version": 2,
        "summary": "User-edited plan about enforcement costs.",
        "scope_notes": "Edited during approval",
        "questions": [
            {
                "question": "What are the enforcement costs of AI regulation?",
                "priority": 90,
            },
            {
                "question": "What evidence supports AI regulation claims?",
                "priority": 80,
            },
            {
                "question": "What limitations affect implementation?",
                "priority": 70,
            },
        ],
    }
    modified = _decide(
        client,
        run["id"],
        second["id"],
        {"decision": "modified", "content": edited_plan},
    )
    assert modified.status_code == 202, modified.text
    assert modified.json()["status"] == "modified"

    outline = _wait_for_approval(client, run["id"], "outline", timeout=90)
    with database.session() as session:
        questions = list(
            session.scalars(
                select(ResearchQuestion.question).where(
                    ResearchQuestion.run_id == UUID(run["id"])
                )
            )
        )
    assert "What are the enforcement costs of AI regulation?" in questions
    assert any(
        approval["kind"] == "plan" and approval["status"] == "modified"
        for approval in _approvals(client, run["id"])
    )

    pending = _approvals(client, run["id"], status="pending")
    assert any(item["id"] == outline["id"] for item in pending)

    cancelled = client.post(f"{API}/runs/{run['id']}/cancel")
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    approvals = _approvals(client, run["id"])
    assert all(
        item["status"] != "pending"
        for item in approvals
    )
    assert any(
        item["id"] == outline["id"] and item["status"] == "cancelled"
        for item in approvals
    )


def test_cost_budget_stops_research_and_is_reported(
    client,
    database,
    project_factory,
):
    project = project_factory(
        research_depth="standard",
        title="Phase 5 budget test",
        budget_usd=0.01,
    )
    with database.session_scope() as session:
        run = RunRepository(session).create(
            UUID(project["id"]),
            RunCreate(idempotency_key=f"budget-{uuid.uuid4()}"),
        )
        run.cost_usd = Decimal("0.02")
        run_id, project_id = run.id, run.project_id

    settings = get_settings()
    get_celery_client().send_task(
        EXECUTE_RESEARCH_RUN_TASK,
        args=[str(run_id), str(project_id)],
        task_id=str(run_id),
        queue=settings.celery_task_default_queue,
    )

    plan = _wait_for_approval(client, str(run_id), "plan")
    assert _decide(
        client,
        str(run_id),
        plan["id"],
        {"decision": "approved"},
    ).status_code == 202
    outline = _wait_for_approval(client, str(run_id), "outline", timeout=90)
    assert _decide(
        client,
        str(run_id),
        outline["id"],
        {"decision": "approved"},
    ).status_code == 202

    completed = _wait_for_run(
        client,
        str(run_id),
        {"completed", "failed"},
        timeout=90,
    )
    assert completed["status"] == "completed", completed

    with database.session() as session:
        report = session.scalar(
            select(Artifact).where(
                Artifact.run_id == run_id,
                Artifact.artifact_type == ArtifactType.RESEARCH_REPORT,
            )
        )
        max_round = session.scalar(
            select(func.max(SearchQuery.round_index)).where(
                SearchQuery.run_id == run_id
            )
        )
    assert report is not None
    assert max_round == 1
    content = get_file_storage().read(report.storage_key).decode("utf-8")
    assert "cost budget was exhausted" in content

    event_messages = " ".join(
        event.get("message") or "" for event in _events(client, str(run_id))
    )
    assert "Search round 2" not in event_messages
