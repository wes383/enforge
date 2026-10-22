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
    ResearchRun,
    RunEvent,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.providers.http import RetryPolicy
from dr_core.providers.llm import OpenAICompatibleLLMProvider
from dr_core.providers.registry import build_provider_registry
from dr_core.research.graph import run_research_workflow
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage

pytestmark = pytest.mark.integration

_WRITE_PROMPTS: list[dict] = []
_SEARCH_REQUESTED = False


def _response(content: str) -> httpx.Response:
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


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    global _SEARCH_REQUESTED
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    if not task.startswith("Write one report section"):
        raise AssertionError(f"unexpected model request: {task[:120]}")
    _WRITE_PROMPTS.append(prompt)
    if (
        prompt.get("search", {}).get("available")
        and not _SEARCH_REQUESTED
    ):
        _SEARCH_REQUESTED = True
        return _response(
            json.dumps(
                {
                    "needs_sources": [
                        {
                            "query": "longitudinal peer review field experiment",
                            "kind": "web",
                            "reason": "the section needs corroboration",
                        }
                    ]
                }
            )
        )
    markers = [item["marker"] for item in prompt.get("evidence", [])]
    if not markers:
        return _response(json.dumps({"markdown": ""}))
    marker = markers[-1] if prompt.get("supplemental_search") else markers[0]
    title = prompt.get("section", {}).get("title", "Section")
    return _response(
        json.dumps(
            {
                "markdown": (
                    f"## {title}\n\n"
                    f"The collected evidence supports this section [{marker}]."
                ),
                "summary": f"Summary of {title}.",
            }
        )
    )


def _settings(**overrides):
    values = {
        "research_provider_mode": "mock",
        "langfuse_enabled": False,
        "research_plan_approval_required": False,
        "research_outline_approval_required": False,
        "research_llm_writing_enabled": True,
        "research_claim_llm_review": False,
        "research_max_search_rounds": 1,
        "research_max_queries_per_round": 3,
        "research_max_sources": 12,
        "research_max_fetches_per_round": 2,
        "research_max_evidence": 20,
        "research_write_search_max_requests_per_section": 2,
        "research_write_search_max_searches": 2,
        "research_write_search_max_sources": 4,
        "research_write_search_max_evidence": 4,
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


def test_workflow_uses_write_time_supplemental_search(database):
    global _SEARCH_REQUESTED
    _WRITE_PROMPTS.clear()
    _SEARCH_REQUESTED = False
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
                title=f"Write search workflow {uuid.uuid4().hex[:8]}",
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
        assert _SEARCH_REQUESTED, [
            (prompt.get("search"), len(prompt.get("evidence", [])))
            for prompt in _WRITE_PROMPTS
        ]
        first_prompt = _WRITE_PROMPTS[0]
        assert first_prompt["prior_sections"]
        assert first_prompt["prior_sections"][0]["summary"]
        assert any(
            "not evidence" in rule for rule in first_prompt["rules"]
        )
        assert any(
            entry["summary"] and "Summary of" in entry["summary"]
            for prompt in _WRITE_PROMPTS
            for entry in prompt.get("prior_sections", [])
        )

        supplement_prompt = next(
            prompt
            for prompt in _WRITE_PROMPTS
            if prompt.get("supplemental_search")
        )
        assert supplement_prompt["search"]["available"] is False
        assert supplement_prompt["supplemental_search"]["sources_added"] >= 1
        assert supplement_prompt["supplemental_search"]["evidence_added"] >= 1

        with database.session() as session:
            events = list(
                session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
            )
        event = next(
            (
                item
                for item in events
                if item.event_type is RunEventType.PROGRESS
                and item.dedupe_key
                and item.dedupe_key.startswith("section:")
                and item.dedupe_key.endswith(":write-search")
            ),
            None,
        )
        assert event is not None
        assert event.payload["sources"] >= 1
        assert event.payload["evidence"] >= 1
        assert event.payload["queries"] == [
            "longitudinal peer review field experiment"
        ]

        used_llm = [
            item
            for item in events
            if item.event_type is RunEventType.SECTION_GENERATED
            and item.payload.get("used_llm") is True
        ]
        assert used_llm
    finally:
        registry.close()
        _cleanup(database, project_id)
