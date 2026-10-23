from __future__ import annotations

import json
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select, text

from dr_core.config import get_settings
from dr_core.db.enums import RunEventType
from dr_core.db.models import (
    Artifact,
    OutlineSectionRecord,
    ResearchProject,
    ResearchRun,
    RunEvent,
    SearchQuery,
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

_LLM_TASKS: list[str] = []
_INVALID_OUTLINE: list[bool] = []
_BODY_TERMS = ["peer review error rates", "peer review bias"]


def _outline_payload() -> str:
    return json.dumps(
        {
            "summary": (
                "Compare peer review models, bias evidence and the limits of "
                "reliability claims."
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
                    "title": "Review models and error rates",
                    "role": "body",
                    "purpose": "Compare what the models change.",
                    "search_terms": [_BODY_TERMS[0]],
                    "target_words": 700,
                },
                {
                    "title": "Reviewer bias",
                    "role": "body",
                    "purpose": "Weigh the bias evidence.",
                    "search_terms": [_BODY_TERMS[1]],
                    "target_words": 700,
                },
                {
                    "title": "Conclusion",
                    "role": "conclusion",
                    "purpose": "Synthesize the evidence.",
                    "search_terms": ["peer review conclusion"],
                    "target_words": 300,
                },
            ],
        },
        ensure_ascii=False,
    )


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    _LLM_TASKS.append(task)
    if task == OUTLINE_PLANNING_TASK:
        content = (
            "the outline is: not usable"
            if _INVALID_OUTLINE
            else _outline_payload()
        )
    else:
        markers = [item["marker"] for item in prompt.get("evidence", [])]
        marker = markers[0] if markers else "S1"
        title = prompt.get("section", {}).get("title", "Section")
        content = json.dumps(
            {
                "markdown": (
                    f"## {title}\n\n"
                    f"The collected evidence supports this section's claim "
                    f"[{marker}]."
                )
            }
        )
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},
        },
    )


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_outline_approval_required": False,
        "research_llm_outline_enabled": True,
        "research_llm_writing_enabled": False,
        "research_claim_llm_review": False,
        "research_agent_enabled": False,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 8,
        "research_max_sources": 20,
        "research_max_fetches_per_round": 2,
        "research_max_evidence": 8,
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


def _create_run(database, topic: str) -> tuple[UUID, UUID]:
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Phase 9 LLM planning {uuid.uuid4().hex[:8]}",
                topic=topic,
                language="en",
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        return project.id, run.id


def _section_titles(database, run_id: UUID) -> list[str]:
    with database.session() as session:
        return list(
            session.scalars(
                select(OutlineSectionRecord.title)
                .where(OutlineSectionRecord.run_id == run_id)
                .order_by(OutlineSectionRecord.priority.desc())
            )
        )


def _planning_event(database, run_id: UUID):
    with database.session() as session:
        return next(
            (
                event
                for event in session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
                if event.event_type is RunEventType.PHASE_COMPLETED
                and "planner" in (event.payload or {})
            ),
            None,
        )


def _query_texts(database, run_id: UUID) -> list[str]:
    with database.session() as session:
        return list(
            session.scalars(
                select(SearchQuery.query_text).where(
                    SearchQuery.run_id == run_id
                )
            )
        )


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
                select(Artifact.storage_key).where(
                    Artifact.project_id == project_id
                )
            )
        )
        session.execute(delete(ResearchProject).where(ResearchProject.id == project_id))
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
    for key in storage_keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except (StorageFileNotFound, StorageError):
            pass


def _run_with_fake_llm(database, topic: str, settings):
    registry = build_provider_registry(settings, use_mocks=True)
    registry.llm = OpenAICompatibleLLMProvider(
        "sk-test-key",
        base_url="https://llm.test/v1",
        model="fake-model",
        retry_policy=RetryPolicy(max_attempts=1),
        client=httpx.Client(
            transport=httpx.MockTransport(_fake_llm_handler),
            timeout=10.0,
        ),
    )
    project_id, run_id = _create_run(database, topic)
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        return outcome, project_id, run_id
    finally:
        registry.close()


def test_model_outline_sections_drive_the_search_queries(database):
    _LLM_TASKS.clear()
    del _INVALID_OUTLINE[:]
    topic = "How does peer review affect research reliability?"
    outcome, project_id, run_id = _run_with_fake_llm(database, topic, _settings())
    try:
        assert outcome.status.value == "completed"
        assert OUTLINE_PLANNING_TASK in _LLM_TASKS

        titles = _section_titles(database, run_id)
        assert len(titles) == 4, titles
        assert "Review models and error rates" in titles
        assert "Reviewer bias" in titles
        assert "How does peer review affect research reliability" not in titles

        event = _planning_event(database, run_id)
        assert event is not None
        assert event.payload["planner"] == "llm"
        assert "Review models and error rates" in event.payload["section_titles"]

        query_texts = _query_texts(database, run_id)
        assert query_texts
        assert set(_BODY_TERMS) <= set(query_texts), (titles, query_texts)
        assert not any("?" in text for text in query_texts)

        with database.session() as session:
            progress = [
                item
                for item in session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
                if item.event_type is RunEventType.PROGRESS
                and isinstance((item.payload or {}).get("model_call"), int)
            ]
        assert progress
        assert {item.payload.get("node") for item in progress} == {"research_agent"}
        assert any(
            item.payload.get("input_tokens") == 40
            and item.payload.get("output_tokens") == 20
            for item in progress
        )
    finally:
        _cleanup(database, project_id)


def test_invalid_model_outline_falls_back_to_the_deterministic_template(database):
    _LLM_TASKS.clear()
    _INVALID_OUTLINE.append(True)
    topic = "How does peer review affect research reliability?"
    outcome, project_id, run_id = _run_with_fake_llm(database, topic, _settings())
    try:
        assert outcome.status.value == "completed"
        assert OUTLINE_PLANNING_TASK in _LLM_TASKS

        titles = _section_titles(database, run_id)
        assert titles
        assert "Review models and error rates" not in titles
        assert "Introduction" in titles

        event = _planning_event(database, run_id)
        assert event is not None
        assert event.payload["planner"] == "template"
    finally:
        del _INVALID_OUTLINE[:]
        _cleanup(database, project_id)
