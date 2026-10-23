from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select

from dr_core.config import get_settings
from dr_core.db.models import (
    ResearchProject,
    Source,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.providers.registry import build_provider_registry
from dr_core.research.models import ResearchBrief
from dr_core.research.tools import ResearchToolbox
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_agent_max_searches": 2,
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def _cleanup(database, project_id: UUID) -> None:
    storage = get_file_storage()
    with database.session_scope() as session:
        storage_keys = list(
            session.scalars(
                select(SourceVersion.storage_key).where(
                    SourceVersion.project_id == project_id,
                    SourceVersion.storage_key.is_not(None),
                )
            )
        )
        session.execute(
            delete(ResearchProject).where(ResearchProject.id == project_id)
        )
    for key in storage_keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except (StorageFileNotFound, StorageError):
            pass


def test_toolbox_ingests_sources_before_returning_them_to_the_model(database):
    settings = _settings()
    registry = build_provider_registry(settings, use_mocks=True)
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Toolbox ingestion {uuid.uuid4().hex[:8]}",
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
        language="en",
    )
    context = SimpleNamespace(
        database=database,
        project_id=project_id,
        run_id=run_id,
        settings=settings,
        registry=registry,
        storage=get_file_storage(),
    )
    try:
        toolbox = ResearchToolbox(
            context,
            brief,
            max_sources=3,
        )
        result = toolbox.execute(
            "search_sources",
            json.dumps(
                {"query": "peer review", "kind": "web", "limit": 5}
            ),
        )
        assert len(result["results"]) == 3
        assert toolbox.source_count == 3
        assert result["remaining_sources"] == 0

        with database.session() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Source)
                    .where(Source.project_id == project_id)
                )
                == 3
            )

        source_id = result["results"][0]["source_id"]
        excerpt = toolbox.execute(
            "read_source",
            json.dumps({"source_id": source_id}),
        )
        assert excerpt["source_id"] == source_id
        assert excerpt["text"]
        assert excerpt["truncated"] in {True, False}

        exhausted = toolbox.execute(
            "search_sources",
            json.dumps(
                {"query": "reviewer bias", "kind": "academic", "limit": 2}
            ),
        )
        assert "source limit reached" in exhausted["error"]

        unknown = toolbox.execute(
            "read_source",
            json.dumps({"source_id": str(uuid.uuid4())}),
        )
        assert "unknown" in unknown["error"]
    finally:
        registry.close()
        _cleanup(database, project_id)
