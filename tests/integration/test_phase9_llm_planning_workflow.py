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
    ResearchProject,
    ResearchQuestion,
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
from dr_core.research.planning import PLANNING_TASK
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_LLM_TASKS: list[str] = []
_INVALID_PLAN: list[bool] = []
_LLM_QUESTION = "Which peer review models most change reported error rates?"
_LLM_SUMMARY = "Compare review models, bias evidence and reliability checks."


def _planning_payload() -> str:
    return json.dumps(
        {
            "summary": _LLM_SUMMARY,
            "questions": [
                {
                    "question": _LLM_QUESTION,
                    "priority": 900,
                    "search_terms": ["peer review error rates"],
                },
                {
                    "question": "What evidence shows peer review bias against new ideas?",
                    "search_terms": ["peer review bias"],
                },
                {
                    "question": "How do editors weigh conflicting peer review reports?",
                    "search_terms": ["editor conflicting reviews"],
                },
                {
                    "question": "Which limits constrain peer review reliability claims?",
                    "search_terms": ["peer review reliability limits"],
                },
                {
                    "question": "What do replication studies say about peer review?",
                    "search_terms": ["peer review replication"],
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
    if task == PLANNING_TASK:
        content = "the plan is: no questions" if _INVALID_PLAN else _planning_payload()
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
        "research_plan_approval_required": False,
        "research_outline_approval_required": False,
        "research_llm_planning_enabled": True,
        "research_llm_writing_enabled": False,
        "research_claim_llm_review": False,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 4,
        "research_max_sources": 6,
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


def _plan_rows(database, run_id: UUID) -> list[str]:
    with database.session() as session:
        return list(
            session.scalars(
                select(ResearchQuestion.question).where(
                    ResearchQuestion.run_id == run_id
                )
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
                and "plan_version" in event.payload
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


def test_workflow_uses_model_generated_research_questions(database):
    _LLM_TASKS.clear()
    del _INVALID_PLAN[:]
    topic = "How does peer review affect research reliability?"
    outcome, project_id, run_id = _run_with_fake_llm(database, topic, _settings())
    try:
        assert outcome.status.value == "completed"
        assert PLANNING_TASK in _LLM_TASKS

        questions = _plan_rows(database, run_id)
        assert _LLM_QUESTION in questions
        # the depth cap keeps 4 of the 5 questions the model proposed
        assert len(questions) == 4
        assert "What do replication studies say about peer review?" not in questions

        event = _planning_event(database, run_id)
        assert event is not None
        assert event.payload["planner"] == "llm"

        query_texts = _query_texts(database, run_id)
        assert query_texts
        assert "peer review error rates" in query_texts
        assert _LLM_QUESTION not in query_texts
    finally:
        _cleanup(database, project_id)


def test_invalid_model_plan_falls_back_to_the_deterministic_template(database):
    _LLM_TASKS.clear()
    _INVALID_PLAN.append(True)
    topic = "How does peer review affect research reliability?"
    outcome, project_id, run_id = _run_with_fake_llm(database, topic, _settings())
    try:
        assert outcome.status.value == "completed"
        assert PLANNING_TASK in _LLM_TASKS

        questions = _plan_rows(database, run_id)
        assert questions
        assert _LLM_QUESTION not in questions
        assert any("core concepts" in question for question in questions)

        event = _planning_event(database, run_id)
        assert event is not None
        assert event.payload["planner"] == "template"
    finally:
        del _INVALID_PLAN[:]
        _cleanup(database, project_id)
