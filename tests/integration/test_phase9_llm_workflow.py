from __future__ import annotations

import json
import uuid
from decimal import Decimal
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

_LLM_CALLS: list[str] = []
_REVIEW_TASK = "Check whether the cited evidence supports the claim."


def _fake_llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    _LLM_CALLS.append(task)
    if _REVIEW_TASK in task:
        content = json.dumps(
            {"status": "PARTIAL", "reason": "cited evidence is indirect"}
        )
    else:
        markers = [item["marker"] for item in prompt.get("evidence", [])]
        marker = markers[0] if markers else "S1"
        title = prompt.get("section", {}).get("title", "Section")
        markdown = (
            f"## {title}\n\n"
            f"The collected evidence supports this section's central claim "
            f"[{marker}]."
        )
        content = json.dumps({"markdown": markdown})
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


def test_workflow_uses_registered_openai_compatible_llm(database):
    _LLM_CALLS.clear()
    review_limit = 2
    settings = get_settings().model_copy(
        update={
            "research_plan_approval_required": False,
            "research_outline_approval_required": False,
            "research_llm_writing_enabled": True,
            "research_claim_llm_review": True,
            "research_claim_llm_review_limit": review_limit,
            "research_provider_mode": "mock",
            "langfuse_enabled": False,
            "llm_cost_per_1m_input_tokens": 2.5,
            "llm_cost_per_1m_output_tokens": 10,
            "research_max_search_rounds": 1,
            "research_max_queries_per_round": 4,
            "research_max_sources": 8,
            "research_max_fetches_per_round": 4,
            "research_max_evidence": 12,
        }
    )
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Phase 9 LLM workflow {uuid.uuid4().hex[:8]}",
                topic="How does peer review affect research reliability?",
                language="en",
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        project_id, run_id = project.id, run.id

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
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"

        with database.session() as session:
            events = list(
                session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id)
                )
            )
        llm_sections = [
            event
            for event in events
            if event.event_type is RunEventType.SECTION_GENERATED
            and event.payload.get("used_llm") is True
        ]
        assert llm_sections, "expected at least one LLM-written section"

        review_calls = [task for task in _LLM_CALLS if _REVIEW_TASK in task]
        assert len(review_calls) <= review_limit
        assert len(_LLM_CALLS) == len(llm_sections) + len(review_calls)

        with database.session() as session:
            run_row = session.get(ResearchRun, run_id)
        assert run_row is not None
        assert run_row.input_tokens == 40 * len(_LLM_CALLS)
        assert run_row.output_tokens == 20 * len(_LLM_CALLS)
        assert run_row.cost_usd == Decimal("0.0003") * len(_LLM_CALLS)
    finally:
        _cleanup(database, project_id)
