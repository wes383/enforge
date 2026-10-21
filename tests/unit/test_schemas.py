from decimal import Decimal

import pytest
from pydantic import ValidationError

from dr_core.db.enums import (
    CitationAuditStatus,
    CitationIssueType,
    SourceOrigin,
    SourceVersionKind,
)
from dr_core.schemas import (
    ArtifactCreate,
    CitationAuditCreate,
    DocumentChunkCreate,
    ProjectCreate,
    ProjectUpdate,
    SourceCreate,
    SourceVersionCreate,
)


def test_project_create_normalizes_and_validates():
    project = ProjectCreate(
        title="  Evidence Desk  ",
        topic="Traceable research",
        budget_usd="12.50",
    )
    assert project.title == "Evidence Desk"
    assert project.budget_usd == Decimal("12.5000")
    assert project.language.value == "zh"

    with pytest.raises(ValidationError):
        ProjectCreate(title="", topic="topic")
    with pytest.raises(ValidationError):
        ProjectCreate(title="Title", topic="topic", budget_usd=0)


def test_project_update_accepts_partial_fields():
    assert ProjectUpdate().model_dump(exclude_unset=True) == {}
    update = ProjectUpdate(target_audience=None, budget_usd="1.25")
    assert update.model_dump(exclude_unset=True) == {
        "target_audience": None,
        "budget_usd": Decimal("1.25"),
    }
    with pytest.raises(ValidationError):
        ProjectUpdate(budget_usd="-1")


def test_source_create_normalizes_identifiers():
    source = SourceCreate(
        title="A paper",
        doi="https://doi.org/10.1234/ABC",
        url="https://Example.com/paper/?utm_source=x",
    )
    assert source.doi == "10.1234/abc"
    assert source.url == "https://example.com/paper"

    with pytest.raises(ValidationError):
        SourceCreate(title="Bad", doi="invalid-doi")
    with pytest.raises(ValidationError):
        SourceCreate(title="Bad", url="javascript:alert(1)")


def test_source_version_requires_storage_for_materialized_content():
    hash_value = "a" * 64
    snippet = SourceVersionCreate(
        content_kind=SourceVersionKind.SEARCH_SNIPPET,
        content_hash=hash_value,
        mime_type="text/plain",
    )
    assert snippet.storage_key is None

    with pytest.raises(ValidationError):
        SourceVersionCreate(
            content_kind=SourceVersionKind.FULL_TEXT,
            content_hash=hash_value,
        )
    with pytest.raises(ValidationError):
        SourceVersionCreate(
            content_kind=SourceVersionKind.FULL_TEXT,
            content_hash=hash_value,
            storage_key="/absolute/original.pdf",
        )
    with pytest.raises(ValidationError):
        SourceVersionCreate(
            content_kind=SourceVersionKind.SEARCH_SNIPPET,
            content_hash="not-a-hash",
        )


def test_uploaded_source_document_is_distinct_from_search_snippet():
    upload = SourceVersionCreate(
        content_kind=SourceVersionKind.UPLOADED_DOCUMENT,
        content_hash="b" * 64,
        storage_key="projects/p/sources/s/original.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
    )
    assert upload.content_kind is SourceVersionKind.UPLOADED_DOCUMENT
    assert SourceOrigin.USER_UPLOAD.value == "user_upload"


def test_chunk_schema_validates_ranges_and_embedding_metadata():
    chunk = DocumentChunkCreate(
        chunk_index=0,
        page_start=2,
        page_end=3,
        text="Evidence text",
        embedding=[0.1, 0.2],
        embedding_model="fake-embedding",
    )
    assert chunk.page_end == 3

    with pytest.raises(ValidationError):
        DocumentChunkCreate(
            chunk_index=0,
            page_start=3,
            page_end=2,
            text="Bad range",
        )
    with pytest.raises(ValidationError):
        DocumentChunkCreate(
            chunk_index=0,
            text="Embedding without model",
            embedding=[0.1],
        )


def test_citation_audit_requires_issue_type_for_failures():
    with pytest.raises(ValidationError):
        CitationAuditCreate(
            draft_section_id="00000000-0000-0000-0000-000000000001",
            status=CitationAuditStatus.FAILED,
            details="Missing locator",
        )

    audit = CitationAuditCreate(
        draft_section_id="00000000-0000-0000-0000-000000000001",
        status=CitationAuditStatus.WARNING,
        issue_type=CitationIssueType.LOCATOR_MISSING,
        details="Missing locator",
    )
    assert audit.issue_type is CitationIssueType.LOCATOR_MISSING


def test_artifact_schema_rejects_absolute_storage_key():
    with pytest.raises(ValidationError):
        ArtifactCreate(
            artifact_type="research_report",
            storage_key="/tmp/report.md",
            mime_type="text/markdown",
            size_bytes=10,
            content_hash="c" * 64,
        )

    artifact = ArtifactCreate(
        artifact_type="research_report",
        storage_key="projects/p/runs/r/report.md",
        mime_type="text/markdown",
        size_bytes=10,
        content_hash="c" * 64,
    )
    assert artifact.storage_key == "projects/p/runs/r/report.md"
