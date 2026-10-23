from __future__ import annotations

import json
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select, text

from dr_core.config import get_settings
from dr_core.db.enums import ArtifactType, DraftSectionStatus, RunEventType
from dr_core.db.models import (
    Artifact,
    DraftSection,
    Outline,
    ResearchProject,
    ResearchRun,
    RunEvent,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.registry import build_provider_registry
from dr_core.research.graph import run_research_workflow
from dr_core.research.planning import OUTLINE_PLANNING_TASK
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_LLM_CALLS: list[str] = []

_MODEL_TITLES = [
    "Peer review reliability",
    "Reviewer bias and error detection",
]


def _outline_reply(*, invalid: bool = False) -> str:
    if invalid:
        return json.dumps({"summary": "Too short."})
    return json.dumps(
        {
            "summary": (
                "The article examines peer review evidence, its reliability "
                "limits and the disagreements that remain."
            ),
            "sections": [
                {
                    "title": "Introduction",
                    "role": "introduction",
                    "purpose": "Frame the reliability issue.",
                    "search_terms": ["peer review reliability"],
                    "target_words": 300,
                },
                {
                    "title": _MODEL_TITLES[0],
                    "role": "body",
                    "purpose": "Weigh what peer review changes.",
                    "search_terms": ["peer review reliability evidence"],
                    "target_words": 600,
                },
                {
                    "title": _MODEL_TITLES[1],
                    "role": "body",
                    "purpose": "Explain reviewer bias and its limits.",
                    "search_terms": ["peer review bias error detection"],
                    "target_words": 600,
                },
                {
                    "title": "Conclusion",
                    "role": "conclusion",
                    "purpose": "Synthesize the evidence.",
                    "search_terms": ["peer review conclusion"],
                    "target_words": 300,
                },
            ],
        }
    )


def _section_reply(prompt: dict) -> str:
    markers = [item["marker"] for item in prompt.get("evidence", [])]
    marker = markers[0] if markers else "S1"
    title = prompt.get("section", {}).get("title", "Section")
    return json.dumps(
        {
            "markdown": (
                f"## {title}\n\n"
                f"The collected evidence supports this section's claim [{marker}]."
            )
        }
    )


def _llm_handler(request: httpx.Request, *, invalid_outline: bool = False):
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    _LLM_CALLS.append(task)
    if task == OUTLINE_PLANNING_TASK:
        content = _outline_reply(invalid=invalid_outline)
    else:
        content = _section_reply(prompt)
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},
        },
    )


def _settings(**overrides):
    values = {
        "research_outline_approval_required": False,
        "research_llm_writing_enabled": True,
        "research_llm_outline_enabled": True,
        "research_agent_enabled": False,
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 4,
        "research_max_sources": 8,
        "research_max_fetches_per_round": 4,
        "research_max_evidence": 12,
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def _start_run(database):
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Phase 9 outline workflow {uuid.uuid4().hex[:8]}",
                topic="How does peer review affect research reliability?",
                language="en",
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        return project.id, run.id


def _registry(handler, settings):
    registry = build_provider_registry(settings, use_mocks=True)
    registry.llm = OpenAICompatibleLLMProvider(
        "sk-test-key",
        base_url="https://llm.test/v1",
        model="fake-model",
        retry_policy=RetryPolicy(max_attempts=1),
        client=httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0),
    )
    return registry


def _cleanup(database, project_id: UUID) -> None:
    storage = get_file_storage()
    with database.session_scope() as session:
        thread_ids = list(
            session.scalars(
                select(ResearchRun.workflow_thread_id).where(
                    ResearchRun.project_id == project_id
                )
            )
        )
        storage_keys = list(
            session.scalars(
                select(SourceVersion.storage_key).where(
                    SourceVersion.project_id == project_id,
                    SourceVersion.storage_key.is_not(None),
                )
            )
        )
        storage_keys.extend(
            session.scalars(
                select(Artifact.storage_key).where(Artifact.project_id == project_id)
            )
        )
        session.execute(delete(ResearchProject).where(ResearchProject.id == project_id))
        for thread_id in thread_ids:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                exists = session.scalar(text("SELECT to_regclass(:name)"), {"name": table})
                if exists:
                    session.execute(
                        text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                        {"thread_id": thread_id},
                    )
    for key in storage_keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except (StorageFileNotFound, StorageError):
            pass


def _outline_payload(database, run_id: UUID) -> dict:
    with database.session() as session:
        events = list(
            session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == RunEventType.PHASE_COMPLETED,
                )
            )
        )
    generated = [
        event for event in events if "outline" in (event.message or "").casefold()
    ]
    assert generated, "expected an outline phase event"
    return generated[-1].payload


def _report_text(database, run_id: UUID) -> str:
    with database.session() as session:
        artifact = session.scalar(
            select(Artifact).where(
                Artifact.run_id == run_id,
                Artifact.artifact_type == ArtifactType.RESEARCH_REPORT,
            )
        )
    return get_file_storage().read(artifact.storage_key).decode("utf-8")


def test_model_outline_sets_the_article_structure(database):
    _LLM_CALLS.clear()
    settings = _settings()
    project_id, run_id = _start_run(database)
    registry = _registry(_llm_handler, settings)
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert _LLM_CALLS.count(OUTLINE_PLANNING_TASK) == 1
        assert _outline_payload(database, run_id)["planner"] == "llm"

        with database.session() as session:
            outline = session.scalar(select(Outline).where(Outline.run_id == run_id))
            sections = list(
                session.scalars(
                    select(DraftSection).where(DraftSection.run_id == run_id)
                )
            )

        titles = [section["title"] for section in outline.content["sections"]]
        assert titles == ["Introduction", *_MODEL_TITLES, "Conclusion"]
        assert all(section.get("section_id") for section in outline.content["sections"])
        assert "<UNTRUSTED_SOURCE_DATA" not in outline.content["summary"]

        drafted = sorted(sections, key=lambda item: item.section_index)
        assert all(
            item.generation_status is not DraftSectionStatus.FAILED for item in drafted
        )
        assert [item.title for item in drafted] == titles

        report = _report_text(database, run_id)
        positions = [report.index(f"## {title}") for title in _MODEL_TITLES]
        assert positions == sorted(positions), "the article follows the model ordering"
        assert report.index("## Introduction") < positions[0]
        assert positions[-1] < report.index("## Conclusion")
    finally:
        _cleanup(database, project_id)


def test_a_rejected_model_outline_falls_back_to_the_template(database):
    _LLM_CALLS.clear()
    settings = _settings()
    project_id, run_id = _start_run(database)

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_handler(request, invalid_outline=True)

    registry = _registry(handler, settings)
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert _LLM_CALLS.count(OUTLINE_PLANNING_TASK) == 1
        assert _outline_payload(database, run_id)["planner"] == "template"

        with database.session() as session:
            outline = session.scalar(select(Outline).where(Outline.run_id == run_id))
        titles = [section["title"] for section in outline.content["sections"]]
        assert not any(title in _MODEL_TITLES for title in titles)
        assert titles[0] == "Introduction"
        assert titles[-1] == "Conclusion"
        assert len(titles) >= 4
    finally:
        _cleanup(database, project_id)
