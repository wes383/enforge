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
    EvidenceItem,
    OutlineSectionRecord,
    ResearchProject,
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
from dr_core.research.evidence_agent import EVIDENCE_TASK
from dr_core.research.graph import run_research_workflow
from dr_core.research.models import ResearchBrief
from dr_core.research.tools import ResearchToolbox
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_PLANNING_TASKS: list[dict] = []
_EVIDENCE_TASKS: list[dict] = []


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


def _planning_payload() -> str:
    return json.dumps(
        {
            "summary": (
                "The material links assistive technology and disability rights "
                "to changes in accessibility law and design practice."
            ),
            "themes": ["assistive technology", "disability rights"],
            "terminology": ["screen reader"],
            "controversies": ["medical versus social model"],
            "sections": [
                {
                    "title": "Assistive technology milestones",
                    "role": "body",
                    "purpose": "Trace the accessibility technology line.",
                    "search_terms": ["accessibility assistive technology"],
                    "target_words": 700,
                },
                {
                    "title": "Law and digital design",
                    "role": "body",
                    "purpose": "Explain the accessibility regulatory turn.",
                    "search_terms": ["accessibility law digital design"],
                    "target_words": 700,
                },
            ],
            "gaps": ["The material is thin on non-Western history."],
            "next_steps": ["Search national disability laws."],
        }
    )


def _evidence_payload(prompt: dict) -> str:
    notes = [
        {
            "section_id": section["section_id"],
            "status": "partial",
            "note": f"Collected material for {section['title']}.",
        }
        for section in prompt["approved_outline"]["sections"]
    ]
    return json.dumps(
        {
            "summary": (
                "Coverage is partial but each approved section now has "
                "retrieved material."
            ),
            "section_notes": notes,
        }
    )


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    messages = body["messages"]
    first_user = next(
        message for message in messages if message.get("role") == "user"
    )
    content = first_user.get("content") or ""
    prompt = json.loads(content)
    tool_messages = [
        message for message in messages if message.get("role") == "tool"
    ]
    if prompt.get("task") == AGENT_TASK:
        _PLANNING_TASKS.append(prompt)
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
                            "planning_search",
                        )
                    ],
                },
                finish_reason="tool_calls",
            )
        return _response({"content": _planning_payload()})
    if prompt.get("task") == EVIDENCE_TASK:
        _EVIDENCE_TASKS.append(prompt)
        sections = prompt["approved_outline"]["sections"]
        if not tool_messages:
            target = sections[0]
            return _response(
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "search_sources",
                            {
                                "query": target["search_terms"][0],
                                "kind": "web",
                                "limit": 2,
                                "section_id": target["section_id"],
                            },
                            "evidence_search",
                        )
                    ],
                },
                finish_reason="tool_calls",
            )
        return _response({"content": _evidence_payload(prompt)})
    raise AssertionError(f"unexpected model request: {content[:160]}")


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_outline_approval_required": False,
        "research_agent_enabled": True,
        "research_agent_max_rounds": 3,
        "research_agent_max_searches": 4,
        "research_agent_max_tool_calls": 4,
        "research_agent_max_sources": 6,
        "research_agent_max_fetches": 0,
        "research_agent_max_citation_calls": 0,
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


def _brief(project_id: UUID, run_id: UUID | None = None) -> ResearchBrief:
    return ResearchBrief(
        project_id=project_id,
        run_id=run_id or uuid.uuid4(),
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
        run_id=uuid.uuid4(),
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


def test_planning_agent_then_evidence_agent_fill_the_approved_outline(database):
    _PLANNING_TASKS.clear()
    _EVIDENCE_TASKS.clear()
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
        assert len(_PLANNING_TASKS) == 2
        assert len(_EVIDENCE_TASKS) == 2
        assert _PLANNING_TASKS[0]["limits"]["sections_hint"][1] >= 3
        assert "questions" not in _PLANNING_TASKS[0]
        approved = _EVIDENCE_TASKS[0]["approved_outline"]["sections"]
        assert len(approved) == 2
        assert all(section["search_terms"] for section in approved)

        with database.session() as session:
            events = list(
                session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
            )
            planning_event = next(
                event
                for event in events
                if event.event_type is RunEventType.PHASE_COMPLETED
                and (event.dedupe_key or "").startswith(
                    "phase:planning-agent:completed"
                )
            )
            evidence_event = next(
                event
                for event in events
                if event.event_type is RunEventType.PHASE_COMPLETED
                and (event.dedupe_key or "").startswith(
                    "phase:evidence-agent:completed"
                )
            )
            assert planning_event.payload["planner"] == "agent"
            assert planning_event.payload["sections"] == 2
            assert planning_event.payload["section_titles"] == [
                "Assistive technology milestones",
                "Law and digital design",
            ]
            assert evidence_event.payload["completed"] is True
            assert evidence_event.payload["section_notes"] == 2
            assert evidence_event.payload["sources"] >= 2
            assert not any("plan_version" in event.payload for event in events)

            tool_events = [
                event
                for event in events
                if event.event_type is RunEventType.PROGRESS
                and (event.payload or {}).get("tool") == "search_sources"
            ]
            planning_tools = [
                event for event in tool_events if "Planning agent" in (event.message or "")
            ]
            evidence_tools = [
                event for event in tool_events if "Evidence agent" in (event.message or "")
            ]
            assert {event.payload["query"] for event in planning_tools} == {
                "accessibility history"
            }
            assert {event.payload["query"] for event in evidence_tools} == {
                "accessibility assistive technology"
            }
            assert all(
                event.payload.get("section_id") for event in evidence_tools
            )

            sections = list(
                session.scalars(
                    select(OutlineSectionRecord).where(
                        OutlineSectionRecord.run_id == run_id
                    )
                )
            )
            assert {section.title for section in sections} == {
                "Assistive technology milestones",
                "Law and digital design",
            }
            evidence = list(
                session.scalars(
                    select(EvidenceItem).where(EvidenceItem.run_id == run_id)
                )
            )
            assert evidence
            assert all(item.outline_section_id for item in evidence)

            drafts = list(
                session.scalars(
                    select(DraftSection).where(DraftSection.run_id == run_id)
                )
            )
            assert len(drafts) == 2

            source_total = session.scalar(
                select(func.count())
                .select_from(Source)
                .where(Source.project_id == project_id)
            )
            assert source_total is not None and source_total >= 2

            run = session.get(ResearchRun, run_id)
            assert run is not None
            # Two model calls per agent: one tool round and one final answer.
            assert run.input_tokens == 40 * 4
            assert run.output_tokens == 20 * 4
    finally:
        registry.close()
        _cleanup(database, project_id)
