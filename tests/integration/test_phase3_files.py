import os
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select

from dr_core.db.models import ResearchProject, SourceVersion
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API = "/api/v1"


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
                "title": f"Phase 3 files {uuid.uuid4().hex[:8]}",
                "topic": "文件上传下载与安全校验",
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


@pytest.fixture
def project(project_factory):
    return project_factory()


@pytest.mark.parametrize(
    ("filename", "content", "mime_type"),
    [
        ("paper.pdf", b"%PDF-1.7\n% test payload\n", "application/pdf"),
        ("page.html", "<html><body>上下文</body></html>".encode(), "text/html"),
        ("notes.md", "# 研究笔记\n\n- 证据\n".encode(), "text/markdown"),
        ("plain.txt", "纯文本内容\n".encode(), "text/plain"),
    ],
)
def test_upload_and_download_roundtrip(
    client,
    project,
    database,
    filename,
    content,
    mime_type,
):
    upload = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": (filename, content, mime_type)},
    )
    assert upload.status_code == 201, upload.text
    uploaded = upload.json()
    assert uploaded["project_id"] == project["id"]
    assert uploaded["original_name"] == filename
    assert uploaded["mime_type"] == mime_type
    assert uploaded["size_bytes"] == len(content)
    assert len(uploaded["sha256"]) == 64
    assert "storage_key" not in uploaded
    assert uploaded["parser_status"] == "pending"

    with database.session_scope() as session:
        version = session.get(SourceVersion, UUID(uploaded["file_id"]))
        assert version is not None
        assert version.storage_key
        assert version.storage_key.startswith(
            f"projects/{project['id']}/sources/"
        )
        assert ".." not in version.storage_key.split("/")
        assert not version.storage_key.startswith("/")
        assert get_file_storage().exists(version.storage_key)

    download = client.get(f"{API}/files/{uploaded['file_id']}/download")
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-type"].startswith(mime_type)
    assert download.headers["content-length"] == str(len(content))
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["cache-control"] == "private, no-store"
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition


def test_upload_rejects_unsafe_or_invalid_content(client, project):
    unsupported = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert unsupported.status_code == 415

    empty = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 400

    fake_pdf = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("fake.pdf", b"<html>not pdf</html>", "application/pdf")},
    )
    assert fake_pdf.status_code == 400

    invalid_utf8 = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("bad.txt", b"text\xff\xfe", "text/plain")},
    )
    assert invalid_utf8.status_code == 400

    nul_bytes = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("nul.txt", b"text\x00inside", "text/plain")},
    )
    assert nul_bytes.status_code == 400

    wrong_field = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"document": ("note.txt", b"hello", "text/plain")},
    )
    assert wrong_field.status_code == 400

    not_multipart = client.post(
        f"{API}/projects/{project['id']}/files",
        json={"file": "hello"},
    )
    assert not_multipart.status_code == 400

    oversized = client.post(
        f"{API}/projects/{project['id']}/files",
        files={
            "file": (
                "large.txt",
                b"a" * (5 * 1024 * 1024 + 1),
                "text/plain",
            )
        },
    )
    assert oversized.status_code == 413


def test_upload_sanitizes_path_traversal_filenames(client, project):
    upload = client.post(
        f"{API}/projects/{project['id']}/files",
        files={
            "file": (
                "../../../etc/passwd.txt",
                b"safe content",
                "text/plain",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    uploaded = upload.json()
    assert uploaded["original_name"] == "passwd.txt"
    assert "/" not in uploaded["original_name"]
    assert "\\" not in uploaded["original_name"]

    download = client.get(f"{API}/files/{uploaded['file_id']}/download")
    assert download.status_code == 200
    assert download.content == b"safe content"
    assert "filename*=UTF-8''passwd.txt" in download.headers["content-disposition"]

    unicode_name = client.post(
        f"{API}/projects/{project['id']}/files",
        files={
            "file": (
                "..\\..\\报告 final.md",
                "# 标题\n".encode(),
                "text/markdown",
            )
        },
    )
    assert unicode_name.status_code == 201, unicode_name.text
    assert unicode_name.json()["original_name"] == "报告 final.md"
    assert "/" not in unicode_name.json()["original_name"]
    assert "\\" not in unicode_name.json()["original_name"]
    encoded = client.get(
        f"{API}/files/{unicode_name.json()['file_id']}/download"
    )
    assert encoded.status_code == 200
    assert "filename*=UTF-8''%E6%8A%A5%E5%91%8A%20final.md" in (
        encoded.headers["content-disposition"]
    )


def test_project_files_listing_returns_uploaded_documents(client, project):
    first = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("first.md", b"# First\n", "text/markdown")},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("second.txt", b"Second\n", "text/plain")},
    )
    assert second.status_code == 201, second.text

    listing = client.get(f"{API}/projects/{project['id']}/files")
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    ids = [item["file_id"] for item in items]
    assert first.json()["file_id"] in ids
    assert second.json()["file_id"] in ids
    for item in items:
        assert item["project_id"] == project["id"]
        assert item["parser_status"] in {"pending", "parsing", "parsed", "failed"}
        assert "storage_key" not in item

    missing = client.get(f"{API}/projects/{uuid.uuid4()}/files")
    assert missing.status_code == 404


def test_files_require_visible_project(client, project):
    upload = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("scoped.txt", b"scoped", "text/plain")},
    )
    assert upload.status_code == 201
    file_id = upload.json()["file_id"]

    assert client.delete(f"{API}/projects/{project['id']}").status_code == 204
    assert client.get(f"{API}/files/{file_id}/download").status_code == 404
    assert client.get(f"{API}/projects/{project['id']}/files").status_code == 404

    missing_project = client.post(
        f"{API}/projects/{uuid.uuid4()}/files",
        files={"file": ("missing.txt", b"missing", "text/plain")},
    )
    assert missing_project.status_code == 404

    deleted_upload = client.post(
        f"{API}/projects/{project['id']}/files",
        files={"file": ("deleted.txt", b"deleted", "text/plain")},
    )
    assert deleted_upload.status_code == 404

    assert client.get(f"{API}/files/{uuid.uuid4()}/download").status_code == 404
