from __future__ import annotations

import uuid
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select

from dr_core.config import get_settings
from dr_core.db.enums import ProjectLanguage
from dr_core.db.models import DocumentChunk, ResearchProject
from dr_core.db.repositories import (
    EvidenceRepository,
    OutlineSectionRepository,
    ProjectRepository,
    RunRepository,
)
from dr_core.providers.registry import build_provider_registry
from dr_core.research.models import OutlineSection, ResearchBrief
from dr_core.research.write_search import WriteSearchSession
from dr_core.research.writing import WriteSearchRequest
from dr_core.schemas import OutlineSectionCreate, ProjectCreate, RunCreate
from dr_core.storage import get_file_storage

pytestmark = pytest.mark.integration


def _cleanup(database, project_id: UUID) -> None:
    with database.session_scope() as session:
        session.execute(
            delete(ResearchProject).where(ResearchProject.id == project_id)
        )


def test_write_search_ingests_indexes_and_extracts_evidence(database):
    settings = get_settings().model_copy(
        update={
            "research_provider_mode": "mock",
            "langfuse_enabled": False,
            "research_write_search_max_requests_per_section": 2,
            "research_write_search_max_searches": 2,
            "research_write_search_max_sources": 6,
            "research_write_search_max_evidence": 6,
            "research_max_sources": 12,
            "research_max_evidence": 12,
        }
    )
    registry = build_provider_registry(settings, use_mocks=True)
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Write search {uuid.uuid4().hex[:8]}",
                topic="How does peer review affect research reliability?",
                language="en",
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        project_id, run_id = project.id, run.id
    brief = ResearchBrief(
        project_id=project_id,
        run_id=run_id,
        title="Peer review",
        topic="How does peer review affect research reliability?",
        language=ProjectLanguage.EN,
    )
    context = SimpleNamespace(
        database=database,
        project_id=project_id,
        run_id=run_id,
        settings=settings,
        registry=registry,
        storage=get_file_storage(),
    )
    section = OutlineSection(
        index=1,
        title="Peer review reliability evidence",
        purpose="Synthesize the evidence.",
        key_claims=["peer review reliability"],
        target_words=300,
    )
    with database.session_scope() as db:
        OutlineSectionRepository(db).create(
            project_id,
            run_id,
            OutlineSectionCreate(
                id=section.section_id,
                title=section.title,
                role=section.role,
                purpose=section.purpose,
                search_terms=section.search_terms,
                target_words=section.target_words,
                priority=section.index,
            ),
        )
    try:
        numbering: dict = {}
        sources: dict = {}
        session_ctx = WriteSearchSession(
            context,
            brief,
            parser=next(iter(registry.parsers), None),
            numbering=numbering,
            sources=sources,
            existing=[],
            evidence_count=0,
        )
        outcome = session_ctx.supplement(
            section,
            [
                WriteSearchRequest(
                    query="peer review reliability",
                    kind="web",
                    reason="initial evidence is missing",
                )
            ],
        )

        assert outcome.searches == 1
        assert outcome.sources_added == 3
        assert outcome.evidence_added == 3
        assert outcome.queries == ("peer review reliability",)
        assert not outcome.errors
        assert len(outcome.evidence) == 3
        assert all(item.source_version_id for item in outcome.evidence)
        assert set(numbering) == {item.source_id for item in outcome.evidence}
        assert set(sources) == set(numbering)

        with database.session() as db:
            stored = EvidenceRepository(db).list_for_run(project_id, run_id)
            assert len(stored) == 3
            chunks = db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.project_id == project_id)
            )
            assert chunks and chunks >= 3

        duplicate = session_ctx.supplement(
            section,
            [WriteSearchRequest(query="peer review reliability", kind="web")],
        )
        assert duplicate.searches == 1
        assert duplicate.sources_added == 0
        assert duplicate.evidence_added == 0
        assert not duplicate.errors

        exhausted = session_ctx.supplement(
            section,
            [WriteSearchRequest(query="reviewer bias", kind="web")],
        )
        assert exhausted.searches == 0
        assert "exhausted" in exhausted.errors[0]
    finally:
        registry.close()
        _cleanup(database, project_id)
