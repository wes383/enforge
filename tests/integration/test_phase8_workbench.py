import os
import re
import time
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select, text

from dr_core.db.models import Artifact, ResearchProject, ResearchRun, SourceVersion
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API = "/api/v1"
POLL_INTERVAL_SECONDS = 0.1
POLL_TIMEOUT_SECONDS = 90


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as http_client:
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
            "title": f"Phase 8 workbench {uuid.uuid4().hex[:8]}",
            "topic": "Enforcement, compliance, and traceable evidence",
            "language": "en",
            "target_words": 1500,
            "date_from": "2020-01-01",
            "date_to": "2026-12-31",
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


def _wait_for_approval(client, run_id, kind, timeout=POLL_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        response = client.get(
            f"{API}/runs/{run_id}/approvals",
            params={"status": "pending"},
        )
        assert response.status_code == 200, response.text
        last = response.json()["items"]
        for approval in last:
            if approval["kind"] == kind:
                return approval
        run = _get_run(client, run_id)
        if run["status"] == "failed":
            raise AssertionError(f"run failed: {run}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"approval {kind} did not appear; pending: {last}")


def _decide(client, run_id, approval_id, payload):
    return client.post(
        f"{API}/runs/{run_id}/approvals/{approval_id}",
        json=payload,
    )


def test_project_options_and_research_workbench(client, project_factory):
    project = project_factory()
    assert project["target_words"] == 1500
    assert project["date_from"] == "2020-01-01"
    assert project["date_to"] == "2026-12-31"

    update = client.patch(
        f"{API}/projects/{project['id']}",
        json={
            "research_scope": "Only peer-reviewed and institutional sources.",
            "target_words": 1800,
            "date_from": "2021-01-01",
        },
    )
    assert update.status_code == 200, update.text
    updated_project = update.json()
    assert updated_project["target_words"] == 1800
    assert updated_project["date_from"] == "2021-01-01"
    assert updated_project["research_scope"].startswith("Only peer-reviewed")

    invalid_update = client.patch(
        f"{API}/projects/{project['id']}",
        json={"date_from": "2026-01-01", "date_to": "2025-01-01"},
    )
    assert invalid_update.status_code == 422

    run = _create_run(client, project["id"])
    outline_approval = _wait_for_approval(client, run["id"], "outline")
    outline_payload = dict(outline_approval["payload"]["outline"])
    outline_payload["title"] = "Edited Phase 8 Report"
    outline_payload["sections"] = [dict(item) for item in outline_payload["sections"]]
    outline_payload["sections"][0]["title"] = "Edited introduction"
    approved_outline = _decide(
        client,
        run["id"],
        outline_approval["id"],
        {"decision": "modified", "content": outline_payload},
    )
    assert approved_outline.status_code == 202, approved_outline.text

    completed = _wait_for_run(
        client,
        run["id"],
        {"completed", "failed"},
        timeout=120,
    )
    assert completed["status"] == "completed", completed

    runs = client.get(f"{API}/projects/{project['id']}/runs")
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1
    assert runs.json()["items"][0]["id"] == run["id"]

    overview = client.get(f"{API}/runs/{run['id']}/overview")
    assert overview.status_code == 200, overview.text
    overview_body = overview.json()
    assert overview_body["run"]["status"] == "completed"
    assert overview_body["project"]["id"] == project["id"]
    assert overview_body["counts"]["sources"] >= 5
    assert overview_body["counts"]["evidence"] > 0
    assert overview_body["counts"]["sections"] >= 3
    assert overview_body["counts"]["artifacts"] == 5
    assert overview_body["search_status"]["queries"] > 0
    assert overview_body["search_status"]["results"] > 0
    assert overview_body["search_status"]["rounds"] >= 1
    assert overview_body["source_status"]["total"] >= 5
    assert overview_body["source_status"]["parsed"] > 0
    assert overview_body["pending_approval"] is None
    assert len(overview_body["outline_sections"]) >= 3
    section_id = overview_body["outline_sections"][0]["id"]

    sources_response = client.get(f"{API}/runs/{run['id']}/sources")
    assert sources_response.status_code == 200, sources_response.text
    sources = sources_response.json()["items"]
    assert sources
    assert any(source["versions"] for source in sources)
    assert all("fetch_status" in source for source in sources)
    assert any(
        version["content_kind"] == "full_text"
        for source in sources
        for version in source["versions"]
    )

    evidence_response = client.get(
        f"{API}/runs/{run['id']}/evidence",
        params={"section_id": section_id, "limit": 100},
    )
    assert evidence_response.status_code == 200, evidence_response.text
    evidence_body = evidence_response.json()
    assert evidence_body["items"]
    assert evidence_body["total"] == len(evidence_body["items"])
    assert all(
        item["outline_section_id"] == section_id
        for item in evidence_body["items"]
    )
    assert all(
        item["locator"].get("source_version_id")
        for item in evidence_body["items"]
    )
    assert all(item["source_version_number"] for item in evidence_body["items"])

    refutations = client.get(
        f"{API}/runs/{run['id']}/evidence",
        params={"evidence_type": "refutation"},
    )
    assert refutations.status_code == 200
    assert all(
        item["evidence_type"] == "refutation"
        for item in refutations.json()["items"]
    )

    outline_response = client.get(f"{API}/runs/{run['id']}/outline")
    assert outline_response.status_code == 200, outline_response.text
    outline_body = outline_response.json()
    assert outline_body["status"] == "approved"
    assert outline_body["outline"]["title"] == "Edited Phase 8 Report"
    assert outline_body["outline"]["sections"][0]["title"] == "Edited introduction"
    target_words = sum(
        section["target_words"] for section in outline_body["outline"]["sections"]
    )
    assert target_words >= 500

    sections_response = client.get(f"{API}/runs/{run['id']}/sections")
    assert sections_response.status_code == 200, sections_response.text
    sections = sections_response.json()["items"]
    assert len(sections) == len(outline_body["outline"]["sections"])
    assert all(item["audit_status"] != "failed" for item in sections)
    assert any(item["audit_status"] == "passed" for item in sections)
    section = sections[0]
    assert section["citation_map"]

    unknown_marker = client.patch(
        f"{API}/runs/{run['id']}/sections/{section['id']}",
        json={
            "title": section["title"],
            "content_markdown": section["content_markdown"] + "\nUnknown [S999].",
        },
    )
    assert unknown_marker.status_code == 422

    marker = next(iter(section["citation_map"]))
    edited_content = (
        section["content_markdown"]
        + f"\nPhase 8 editor note with a traceable citation [{marker}]."
    )
    edited = client.patch(
        f"{API}/runs/{run['id']}/sections/{section['id']}",
        json={"title": section["title"], "content_markdown": edited_content},
    )
    assert edited.status_code == 200, edited.text
    edited_section = edited.json()
    assert edited_section["version"] == section["version"] + 1
    assert edited_section["id"] != section["id"]
    assert edited_section["generation_status"] == "drafted"
    assert edited_section["content_markdown"] == edited_content
    used_markers = {
        f"S{number}" for number in re.findall(r"\[S(\d+)\]", edited_content)
    }
    assert set(edited_section["citation_map"]) == used_markers
    assert all(
        edited_section["citation_map"][used] == section["citation_map"][used]
        for used in used_markers
    )

    outline_after_edit = client.get(f"{API}/runs/{run['id']}/outline").json()
    assert outline_after_edit["outline"]["sections"][0]["status"] == "drafted"

    claims_response = client.get(f"{API}/runs/{run['id']}/claims")
    assert claims_response.status_code == 200, claims_response.text
    claims = claims_response.json()["items"]
    assert claims
    assert any(claim["evidence"] for claim in claims)

    artifacts_response = client.get(f"{API}/runs/{run['id']}/artifacts")
    assert artifacts_response.status_code == 200, artifacts_response.text
    artifacts = artifacts_response.json()["items"]
    assert {item["artifact_type"] for item in artifacts} == {
        "research_report",
        "reference_list",
        "markdown_export",
        "evidence_matrix",
        "citation_audit",
    }
    assert all(item["version"] == 1 for item in artifacts)
    report = next(
        item for item in artifacts if item["artifact_type"] == "research_report"
    )
    reference_list = next(
        item for item in artifacts if item["artifact_type"] == "reference_list"
    )

    report_download = client.get(
        f"{API}/runs/{run['id']}/artifacts/{report['id']}/download"
    )
    assert report_download.status_code == 200
    assert "attachment" in report_download.headers["content-disposition"]
    assert report_download.headers["x-content-type-options"] == "nosniff"
    assert b"# " in report_download.content
    reference_download = client.get(
        f"{API}/runs/{run['id']}/artifacts/{reference_list['id']}/download"
    )
    assert reference_download.status_code == 200
    assert re.search(rb"\[S\d+\]", reference_download.content)

    verification = client.get(f"{API}/runs/{run['id']}/verification")
    assert verification.status_code == 200, verification.text
    verification_body = verification.json()
    assert verification_body["available"] is True
    assert verification_body["citation_audit"]["passed"] >= 1
    assert verification_body["reference_audit"]["dangling_markers"] == []
    assert verification_body["claims"]

    export = client.post(f"{API}/runs/{run['id']}/export")
    assert export.status_code == 201, export.text
    export_body = export.json()
    assert export_body["section_count"] == len(sections)
    assert len(export_body["artifacts"]) == 5
    assert all(item["version"] == 2 for item in export_body["artifacts"])
    assert export_body["citation_audit"]["failed"] == 0
    assert export_body["reference_audit"]["dangling_markers"] == []
    assert export_body["claims"]

    sections_after_export = client.get(f"{API}/runs/{run['id']}/sections").json()[
        "items"
    ]
    edited_after_export = next(
        item
        for item in sections_after_export
        if item["section_index"] == edited_section["section_index"]
    )
    assert edited_after_export["id"] == edited_section["id"]
    assert edited_after_export["generation_status"] == "verified"
    assert edited_after_export["audit_status"] == "passed"

    verification_after_export = client.get(
        f"{API}/runs/{run['id']}/verification"
    ).json()
    assert verification_after_export["available"] is True
    assert verification_after_export["generated_at"]

    second_export = client.post(f"{API}/runs/{run['id']}/export")
    assert second_export.status_code == 201, second_export.text
    assert all(
        item["version"] == 3 for item in second_export.json()["artifacts"]
    )

    events = client.get(
        f"{API}/runs/{run['id']}/events",
        params={"limit": 1000},
    ).json()["items"]
    assert any(
        event["message"] == "Report export regenerated" for event in events
    )
