import pytest

import dr_core.db.models  # noqa: F401
from dr_core.config import get_settings
from dr_core.db.base import Base

REQUIRED_TABLES = {
    "research_projects",
    "research_runs",
    "research_questions",
    "search_queries",
    "sources",
    "source_versions",
    "document_chunks",
    "evidence_items",
    "claims",
    "claim_evidence",
    "outlines",
    "draft_sections",
    "citation_audits",
    "run_events",
    "artifacts",
}


def test_all_required_tables_exist():
    assert REQUIRED_TABLES <= set(Base.metadata.tables)


def test_every_table_except_projects_is_project_scoped():
    for table in Base.metadata.sorted_tables:
        if table.name == "research_projects":
            continue
        assert "project_id" in table.c, f"{table.name} lacks project_id"


def test_artifact_and_source_version_keys_are_constrained():
    for table_name in ("artifacts", "source_versions"):
        checks = {
            constraint.name
            for constraint in Base.metadata.tables[table_name].constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        assert any(
            name and "relative_storage_key" in name for name in checks
        ), f"{table_name} lacks a relative storage key check"


def test_document_chunk_vector_dimension_matches_settings():
    embedding = Base.metadata.tables["document_chunks"].c.embedding
    assert embedding.type.dim == get_settings().embedding_dimension


def test_artifacts_do_not_store_absolute_paths():
    artifacts = Base.metadata.tables["artifacts"]
    assert "storage_key" in artifacts.c
    assert "uri" not in artifacts.c
    assert not any("s3" in column.name.lower() for column in artifacts.c)


@pytest.mark.parametrize(
    "table_name",
    sorted(REQUIRED_TABLES - {"research_projects"}),
)
def test_project_foreign_key_cascades(table_name):
    table = Base.metadata.tables[table_name]
    project_fk = next(
        fk
        for fk in table.foreign_keys
        if fk.column.table.name == "research_projects"
    )
    assert project_fk.ondelete == "CASCADE"
