from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, func, select, text

from dr_core.config import get_settings
from dr_core.db.enums import RunEventType, SourceOrigin
from dr_core.db.models import (
    Artifact,
    ResearchProject,
    ResearchRun,
    RunEvent,
    Source,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.registry import build_provider_registry
from dr_core.research.graph import run_research_workflow
from dr_core.research.models import ResearchBrief
from dr_core.research.planning import PLANNING_TASK
from dr_core.research.tools import ResearchToolbox
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_RECON_TASKS: list[str] = []
_PLANNING_PROMPTS: list[dict] = []


def _tool_call(name: str, arguments: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _response(message: dict, *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [
                {
                    "message": {"role": "assistant", **message},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20},
        },
    )


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    messages = body["messages"]
    first_user = next(
        message for message in messages if message.get("role") == "user"
    )
    if '"task": "Gather current context' in (first_user.get("content") or ""):
        _RECON_TASKS.append(first_user["content"])
        tool_messages = [
            message for message in messages if message.get("role") == "tool"
        ]
        if not tool_messages:
            return _response(
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "search_sources",
                            {
                                "query": "peer review reliability",
                                "kind": "web",
                                "limit": 2,
                            },
                            "call_web",
                        )
                    ],
                },
                finish_reason="tool_calls",
            )
        if len(tool_messages) == 1:
            return _response(
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "search_sources",
                            {
                                "query": "peer review error rates",
                                "kind": "academic",
                                "limit": 2,
                            },
                            "call_academic",
                        )
                    ],
                },
                finish_reason="tool_calls",
            )
        return _response(
            {
                "content": json.dumps(
                    {
                        "summary": (
                            "The literature debates reviewer bias, error "
                            "detection and the limits of single studies."
                        ),
                        "themes": ["reviewer bias", "error detection"],
                        "terminology": ["single-blind review"],
                        "controversies": ["whether review improves reliability"],
                    }
                )
            }
        )

    content = messages[-1].get("content") or ""
    prompt = json.loads(content)
    if prompt.get("task") == PLANNING_TASK:
        _PLANNING_PROMPTS.append(prompt)
        return _response(
            {
                "content": json.dumps(
                    {
                        "summary": "Compare review models, bias and reliability.",
                        "questions": [
                            {
                                "question": (
                                    "Which peer review models change reported "
                                    "error rates?"
                                ),
                                "priority": 900,
                                "search_terms": ["peer review error rates"],
                            },
                            {
                                "question": (
                                    "What evidence links reviewer bias to "
                                    "reliability?"
                                ),
                                "priority": 700,
                                "search_terms": ["reviewer bias reliability"],
                            },
                            {
                                "question": (
                                    "Which limits constrain peer review "
                                    "reliability claims?"
                                ),
                                "priority": 500,
                                "search_terms": ["peer review limits"],
                            },
                        ],
                    }
                )
            }
        )
    raise AssertionError(f"unexpected model request: {content[:200]}")


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_plan_approval_required": False,
        "research_outline_approval_required": False,
        "research_llm_planning_enabled": True,
        "research_llm_recon_enabled": True,
        "research_llm_writing_enabled": False,
        "research_claim_llm_review": False,
        "research_recon_max_rounds": 2,
        "research_recon_max_sources": 4,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 3,
        "research_max_sources": 12,
        "research_max_fetches_per_round": 2,
        "research_max_evidence": 6,
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


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
        session.execute(
            delete(ResearchProject).where(ResearchProject.id == project_id)
        )
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


def test_toolbox_ingests_sources_before_returning_them_to_the_model(
    database,
):
    settings = _settings(research_recon_max_searches=2)
    registry = build_provider_registry(settings, use_mocks=True)
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Recon toolbox {uuid.uuid4().hex[:8]}",
                topic="How does peer review affect research reliability?",
                language="en",
            )
        )
        project_id = project.id
    brief = ResearchBrief(
        project_id=project_id,
        run_id=uuid.uuid4(),
        title="Peer review",
        topic="How does peer review affect research reliability?",
        language="en",
    )
    context = SimpleNamespace(
        database=database,
        project_id=project_id,
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


def test_workflow_reconnaissance_grounds_the_model_plan(database):
    _RECON_TASKS.clear()
    _PLANNING_PROMPTS.clear()
    settings = _settings()
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
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Recon workflow {uuid.uuid4().hex[:8]}",
                topic="How does peer review affect research reliability?",
                language="en",
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        project_id, run_id = project.id, run.id
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert len(_RECON_TASKS) == 3
        assert len(_PLANNING_PROMPTS) == 1
        assert "reconnaissance" in _PLANNING_PROMPTS[0]
        assert _PLANNING_PROMPTS[0]["reconnaissance"].startswith(
            '<UNTRUSTED_SOURCE_DATA label="reconnaissance"'
        )

        with database.session() as session:
            recon_event = next(
                (
                    event
                    for event in session.scalars(
                        select(RunEvent).where(RunEvent.run_id == run_id)
                    )
                    if event.event_type is RunEventType.PHASE_COMPLETED
                    and event.dedupe_key == "phase:recon:completed"
                ),
                None,
            )
            assert recon_event is not None
            assert recon_event.payload["completed"] is True
            assert recon_event.payload["sources"] >= 4

            planning_event = next(
                event
                for event in session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
                if event.event_type is RunEventType.PHASE_COMPLETED
                and "plan_version" in event.payload
            )
            assert planning_event.payload["recon_sources"] >= 4

            recon_sources = session.scalar(
                select(func.count())
                .select_from(Source)
                .where(
                    Source.project_id == project_id,
                    Source.source_origin.in_(
                        [SourceOrigin.WEB_SEARCH, SourceOrigin.ACADEMIC_SEARCH]
                    ),
                )
            )
            assert recon_sources and recon_sources >= 4

            run = session.get(ResearchRun, run_id)
            assert run is not None
            assert run.input_tokens == 40 * 4
            assert run.output_tokens == 20 * 4
    finally:
        registry.close()
        _cleanup(database, project_id)
