from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, func, select, text

from dr_core.config import get_settings
from dr_core.db.enums import RunEventType, SearchProviderName
from dr_core.db.models import (
    Artifact,
    DraftSection,
    ResearchProject,
    ResearchQuestion,
    ResearchRun,
    RunEvent,
    Source,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockDocumentParser,
    MockEmbeddingProvider,
    MockLLMProvider,
    MockSearchProvider,
    MockWebContentProvider,
)
from dr_core.providers.models import AcademicReference, AcademicResult
from dr_core.providers.registry import ProviderRegistry, build_provider_registry
from dr_core.research.agent import AGENT_TASK
from dr_core.research.graph import run_research_workflow
from dr_core.research.models import ResearchBrief
from dr_core.research.tools import ResearchToolbox
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_AGENT_TASKS: list[dict] = []


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


def _final_payload() -> str:
    return json.dumps(
        {
            "summary": (
                "The material links assistive technology, disability rights "
                "and accessibility law to changes in design practice."
            ),
            "themes": ["assistive technology", "disability rights"],
            "terminology": ["screen reader"],
            "controversies": ["medical versus social model"],
            "questions": [
                {
                    "question": (
                        "Which accessibility milestones changed assistive "
                        "technology?"
                    ),
                    "priority": 900,
                    "search_terms": ["accessibility assistive technology"],
                },
                {
                    "question": (
                        "How did accessibility law change digital design?"
                    ),
                    "priority": 700,
                    "search_terms": ["accessibility law digital design"],
                },
            ],
            "outline": [
                {
                    "title": "Assistive technology milestones",
                    "purpose": "Trace the technology line.",
                    "question_index": 0,
                },
                {
                    "title": "Law and digital design",
                    "purpose": "Explain the regulatory turn.",
                    "question_index": 1,
                },
            ],
            "gaps": ["The material is thin on non-Western history."],
            "next_steps": ["Search national disability laws."],
        }
    )


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    messages = body["messages"]
    first_user = next(
        message for message in messages if message.get("role") == "user"
    )
    if AGENT_TASK not in (first_user.get("content") or ""):
        raise AssertionError("unexpected model request")
    _AGENT_TASKS.append(json.loads(first_user["content"]))
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
                            "query": "accessibility history",
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
                            "query": "accessibility law",
                            "kind": "academic",
                            "limit": 2,
                        },
                        "call_academic",
                    )
                ],
            },
            finish_reason="tool_calls",
        )
    return _response({"content": _final_payload()})


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_plan_approval_required": False,
        "research_outline_approval_required": False,
        "research_agent_enabled": True,
        "research_agent_max_rounds": 3,
        "research_agent_max_searches": 4,
        "research_agent_max_tool_calls": 4,
        "research_agent_max_sources": 6,
        "research_agent_max_fetches": 0,
        "research_agent_max_citation_calls": 0,
        "research_llm_planning_enabled": False,
        "research_llm_recon_enabled": False,
        "research_llm_writing_enabled": False,
        "research_claim_llm_review": False,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 2,
        "research_max_sources": 10,
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


def _project(database) -> UUID:
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Research agent {uuid.uuid4().hex[:8]}",
                topic="How have people worked for accessibility?",
                language="en",
            )
        )
        return project.id


def _brief(project_id: UUID) -> ResearchBrief:
    return ResearchBrief(
        project_id=project_id,
        run_id=uuid.uuid4(),
        title="Accessibility efforts",
        topic="How have people worked for accessibility?",
        language="en",
    )


def test_toolbox_follows_citations_and_fetches_full_pages(database):
    project_id = _project(database)
    settings = _settings()
    academic = MockAcademicSearchProvider(
        references=[
            AcademicReference(
                title="Earlier accessibility work",
                doi="10.1000/earlier",
                year=1999,
            ),
            AcademicReference(
                unstructured="Braille education in the nineteenth century",
            ),
        ],
        citations=[
            AcademicResult(
                provider=SearchProviderName.MOCK,
                title="Citing accessibility work",
                doi="10.1000/citing",
                published_year=2021,
            )
        ],
    )
    registry = ProviderRegistry(
        web_search=[MockSearchProvider()],
        academic_search=[academic],
        web_content=[MockWebContentProvider()],
        parsers=[MockDocumentParser()],
        embedding=MockEmbeddingProvider(dimension=settings.embedding_dimension),
        llm=MockLLMProvider(),
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
            _brief(project_id),
            max_sources=8,
            max_searches=4,
            max_fetches=1,
            max_citation_calls=2,
        )
        academic_hits = toolbox.execute(
            "search_sources",
            json.dumps(
                {"query": "accessibility law", "kind": "academic", "limit": 2}
            ),
        )
        source_id = academic_hits["results"][0]["source_id"]

        references = toolbox.execute(
            "follow_citations",
            json.dumps({"source_id": source_id, "direction": "references"}),
        )
        assert references["from_source_id"] == source_id
        assert references["results"][0]["title"] == "Earlier accessibility work"
        assert references["results"][0]["via_source_id"] == source_id
        assert references["leads"] == [
            "Braille education in the nineteenth century"
        ]

        citations = toolbox.execute(
            "follow_citations",
            json.dumps({"source_id": source_id, "direction": "citations"}),
        )
        assert citations["results"][0]["title"] == "Citing accessibility work"
        assert toolbox.citation_calls == 2
        assert toolbox.source_count == 4

        web_hits = toolbox.execute(
            "search_sources",
            json.dumps(
                {"query": "accessibility timeline", "kind": "web", "limit": 2}
            ),
        )
        web_id = web_hits["results"][0]["source_id"]
        fetched = toolbox.execute(
            "fetch_source",
            json.dumps({"source_id": web_id}),
        )
        assert fetched["status"] == "success"
        assert fetched["text"]
        assert fetched["fetches_left"] == 0
        assert toolbox.fetches == 1
        assert any(
            str(record.source_id) == web_id and record.has_full_text
            for record in toolbox.records
        )

        exhausted = toolbox.execute(
            "fetch_source",
            json.dumps({"source_id": web_hits["results"][1]["source_id"]}),
        )
        assert "fetch budget exhausted" in exhausted["error"]
    finally:
        registry.close()
        _cleanup(database, project_id)


def test_workflow_uses_the_research_agent_and_skips_planning(database):
    _AGENT_TASKS.clear()
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
    project_id = _project(database)
    with database.session_scope() as session:
        run = RunRepository(session).create(project_id, RunCreate())
        run_id = run.id
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert len(_AGENT_TASKS) == 3
        assert _AGENT_TASKS[0]["limits"]["questions"] >= 2

        with database.session() as session:
            events = list(
                session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
            )
            agent_event = next(
                (
                    event
                    for event in events
                    if event.event_type is RunEventType.PHASE_COMPLETED
                    and (event.dedupe_key or "").startswith(
                        "phase:research-agent:completed"
                    )
                ),
                None,
            )
            assert agent_event is not None
            assert agent_event.payload["agent"] is True
            assert agent_event.payload["sources"] >= 4
            assert agent_event.payload["questions"] == 2
            assert agent_event.payload["outline_sections"] == 2
            assert agent_event.payload["searches"] == 2
            assert not any("plan_version" in event.payload for event in events)

            tool_events = [
                event
                for event in events
                if event.event_type is RunEventType.PROGRESS
                and (event.payload or {}).get("tool") == "search_sources"
            ]
            assert {event.payload["query"] for event in tool_events} == {
                "accessibility history",
                "accessibility law",
            }
            assert all("searched" in (event.message or "") for event in tool_events)

            questions = list(
                session.scalars(
                    select(ResearchQuestion).where(
                        ResearchQuestion.run_id == run_id
                    )
                )
            )
            assert len(questions) == 2
            source_total = session.scalar(
                select(func.count())
                .select_from(Source)
                .where(Source.project_id == project_id)
            )
            assert source_total is not None
            assert source_total >= agent_event.payload["sources"] >= 4
            sections = list(
                session.scalars(
                    select(DraftSection).where(DraftSection.run_id == run_id)
                )
            )
            assert sections

            run = session.get(ResearchRun, run_id)
            assert run is not None
            assert run.input_tokens == 40 * 3
            assert run.output_tokens == 20 * 3
    finally:
        registry.close()
        _cleanup(database, project_id)
