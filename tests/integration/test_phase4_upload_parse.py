import io
import os
import time
import uuid
from uuid import UUID

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import delete, select

from dr_core.config import get_settings
from dr_core.db.models import ResearchProject, SourceVersion
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage
from dr_core.tasks import PARSE_DOCUMENT_TASK, get_celery_client

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API = "/api/v1"
POLL_TIMEOUT_SECONDS = 40


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as http_client:
        yield http_client


@pytest.fixture
def project_factory(client, database):
    created: list[UUID] = []

    def factory():
        response = client.post(
            f"{API}/projects",
            json={
                "title": f"Phase 4 parse {uuid.uuid4().hex[:8]}",
                "topic": "本地文档解析与状态持久化",
                "language": "zh",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        created.append(UUID(body["id"]))
        return body

    yield factory

    if not created:
        return
    storage = get_file_storage()
    with database.session_scope() as session:
        keys = list(
            session.scalars(
                select(SourceVersion.storage_key).where(
                    SourceVersion.project_id.in_(created),
                    SourceVersion.storage_key.is_not(None),
                )
            )
        )
        session.execute(
            delete(ResearchProject).where(ResearchProject.id.in_(created))
        )
    for key in keys:
        try:
            storage.delete(key)
        except (StorageFileNotFound, StorageError):
            pass


def _pdf_with_text(text: str, title: str | None = None) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    if title:
        writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _upload(client, project_id, filename, content, mime_type):
    response = client.post(
        f"{API}/projects/{project_id}/files",
        files={"file": (filename, content, mime_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for_file(client, file_id, statuses=None, timeout=POLL_TIMEOUT_SECONDS):
    statuses = statuses or {"parsed", "failed"}
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"{API}/files/{file_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["parser_status"] in statuses:
            return last
        time.sleep(0.1)
    raise AssertionError(f"file {file_id} did not reach {statuses}; last: {last}")


@pytest.mark.parametrize(
    ("filename", "content", "mime_type", "expected_title"),
    [
        (
            "研究笔记.html",
            (
                "<html><head><title>Uploaded HTML</title></head><body><main>"
                "<h1>Overview</h1><p>"
                + "Evidence paragraph. " * 40
                + "</p><h2>Methods</h2><p>Method paragraph.</p>"
                "<table><tr><th>Metric</th><th>Value</th></tr>"
                "<tr><td>Accuracy</td><td>0.9</td></tr></table>"
                "</main></body></html>"
            ).encode(),
            "text/html",
            "Uploaded HTML",
        ),
        (
            "notes.md",
            (
                "# Uploaded Markdown\n\n"
                "Paragraph one.\n\n"
                "## Findings\n\n"
                "Paragraph two.\n"
            ).encode(),
            "text/markdown",
            "Uploaded Markdown",
        ),
        (
            "plain.txt",
            b"First paragraph.\n\nSecond paragraph.\n",
            "text/plain",
            None,
        ),
        (
            "paper.pdf",
            _pdf_with_text("Uploaded PDF text", title="Uploaded PDF"),
            "application/pdf",
            "Uploaded PDF",
        ),
    ],
)
def test_uploaded_documents_are_parsed_by_worker(
    client,
    project_factory,
    database,
    filename,
    content,
    mime_type,
    expected_title,
):
    project = project_factory()
    uploaded = _upload(client, project["id"], filename, content, mime_type)
    assert uploaded["parser_status"] in {"pending", "parsing", "parsed"}
    assert uploaded["parser_version"] in {None, "local-1.0"}

    file_info = _wait_for_file(client, uploaded["file_id"], {"parsed"})
    assert file_info["parser_status"] == "parsed"
    assert file_info["parser_version"] == "local-1.0"
    assert file_info["parse_error"] is None
    assert file_info["original_name"] == filename

    with database.session_scope() as session:
        version = session.get(SourceVersion, UUID(uploaded["file_id"]))
        assert version is not None
        assert version.parser_status.value == "parsed"
        assert version.version_metadata["parse"]["parser_version"] == "local-1.0"
        assert version.version_metadata["parse"]["title"] == expected_title
        if mime_type == "application/pdf":
            assert version.version_metadata["parse"]["page_count"] == 1
            assert version.version_metadata["parse"]["character_count"] > 0
        if mime_type == "text/html":
            assert version.version_metadata["parse"]["section_count"] == 2
            assert version.version_metadata["parse"]["tables_status"] == "best_effort"
        if mime_type == "text/plain":
            assert version.version_metadata["parse"]["section_count"] == 0


def test_corrupt_pdf_records_readable_parse_failure(client, project_factory):
    project = project_factory()
    uploaded = _upload(
        client,
        project["id"],
        "corrupt.pdf",
        b"%PDF-1.4\nthis is not a valid pdf body",
        "application/pdf",
    )
    failed = _wait_for_file(client, uploaded["file_id"], {"failed"})
    assert failed["parser_status"] == "failed"
    assert failed["parser_version"] == "local-1.0"
    assert failed["parse_error"]
    assert "PDF" in failed["parse_error"]

    download = client.get(f"{API}/files/{uploaded['file_id']}/download")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4\nthis is not a valid pdf body"


def test_duplicate_parse_delivery_is_idempotent(client, project_factory, database):
    project = project_factory()
    uploaded = _upload(
        client,
        project["id"],
        "idempotent.md",
        "# Idempotent\n\nA paragraph.\n",
        "text/markdown",
    )
    parsed = _wait_for_file(client, uploaded["file_id"], {"parsed"})
    assert parsed["parser_version"] == "local-1.0"

    with database.session_scope() as session:
        before = session.get(SourceVersion, UUID(uploaded["file_id"]))
        before_metadata = dict(before.version_metadata)

    task = get_celery_client().send_task(
        PARSE_DOCUMENT_TASK,
        args=[uploaded["file_id"], project["id"]],
        queue=get_settings().celery_parse_queue,
    )
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    task_state = None
    while time.monotonic() < deadline:
        response = client.get(f"{API}/dev/tasks/{task.id}")
        assert response.status_code == 200
        task_state = response.json()
        if task_state["state"] in {"SUCCESS", "FAILURE"}:
            break
        time.sleep(0.1)
    assert task_state["state"] == "SUCCESS", task_state
    assert task_state["result"]["status"] == "parsed"

    with database.session_scope() as session:
        after = session.get(SourceVersion, UUID(uploaded["file_id"]))
        assert after.version_metadata == before_metadata
        assert after.parser_status.value == "parsed"


def test_file_metadata_is_hidden_after_project_soft_delete(client, project_factory):
    project = project_factory()
    uploaded = _upload(
        client,
        project["id"],
        "hidden.txt",
        b"hidden content",
        "text/plain",
    )
    _wait_for_file(client, uploaded["file_id"], {"parsed", "failed"})
    assert client.delete(f"{API}/projects/{project['id']}").status_code == 204
    assert client.get(f"{API}/files/{uploaded['file_id']}").status_code == 404
