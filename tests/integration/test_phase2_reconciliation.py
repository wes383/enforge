import hashlib
import uuid

import pytest

from dr_core.db.enums import ArtifactType, SourceVersionKind
from dr_core.db.repositories import (
    ArtifactRepository,
    RunRepository,
    SourceRepository,
    SourceVersionRepository,
)
from dr_core.maintenance import reconcile_files
from dr_core.schemas import (
    ArtifactCreate,
    RunCreate,
    SourceCreate,
    SourceVersionCreate,
)
from dr_core.storage import LocalFileStorage

pytestmark = pytest.mark.integration


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_reconciliation_reports_missing_and_orphan_files(tmp_path, session, project):
    storage = LocalFileStorage(tmp_path / "files")
    run = RunRepository(session).create(project.id, RunCreate())
    source = SourceRepository(session).create(
        project.id,
        SourceCreate(title="Reconciliation source"),
    )
    version_key = (
        f"projects/{project.id}/sources/{source.id}/"
        f"{uuid.uuid4().hex}/original.txt"
    )
    version = SourceVersionRepository(session).create(
        project.id,
        source.id,
        SourceVersionCreate(
            content_kind=SourceVersionKind.FULL_TEXT,
            content_hash=_hash("original"),
            storage_key=version_key,
            mime_type="text/plain",
            size_bytes=8,
        ),
    )
    artifact_key = (
        f"projects/{project.id}/runs/{run.id}/"
        f"{uuid.uuid4().hex}/report.md"
    )
    artifact = ArtifactRepository(session).register(
        project.id,
        run.id,
        ArtifactCreate(
            artifact_type=ArtifactType.MARKDOWN_EXPORT,
            storage_key=artifact_key,
            mime_type="text/markdown",
            size_bytes=6,
            content_hash=_hash("report"),
        ),
    )

    storage.save(artifact_key, b"report")
    orphan_key = f"projects/{project.id}/orphans/{uuid.uuid4().hex}.bin"
    ignored_key = f"_health/{uuid.uuid4().hex}.probe"
    storage.save(orphan_key, b"orphan")
    storage.save(ignored_key, b"ok")

    report = reconcile_files(session, storage)
    assert report.checked_files >= 2
    missing_keys = [item.storage_key for item in report.missing_files]
    assert version_key in missing_keys
    assert artifact_key not in missing_keys
    missing_version = next(
        item for item in report.missing_files if item.storage_key == version_key
    )
    assert missing_version.record_id == version.id
    assert missing_version.record_type == "source_version"
    assert report.orphan_keys == (orphan_key,)
    assert not report.clean

    storage.save(version_key, b"original")
    report_after_restore = reconcile_files(session, storage)
    assert version_key not in [
        item.storage_key for item in report_after_restore.missing_files
    ]
    assert report_after_restore.orphan_keys == (orphan_key,)

    storage.delete(orphan_key)
    clean_report = reconcile_files(session, storage)
    assert version_key not in [
        item.storage_key for item in clean_report.missing_files
    ]
    assert artifact_key not in [
        item.storage_key for item in clean_report.missing_files
    ]
    assert clean_report.orphan_keys == ()
    assert clean_report.checked_files >= 2
    assert artifact.id is not None
