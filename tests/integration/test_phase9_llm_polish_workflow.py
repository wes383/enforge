from __future__ import annotations

import json
import re
import uuid
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete, select, text

from dr_core.config import get_settings
from dr_core.db.enums import ArtifactType, RunEventType
from dr_core.db.models import (
    Artifact,
    CitationAudit,
    DraftSection,
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
_POLISH_TASK = "Rewrite the finished article so it reads as one fluent, coherent piece."
_MARKER_RE = re.compile(r"\[S\d+\]")
_TAG_RE = re.compile(r"</?UNTRUSTED_SOURCE_DATA[^>]*>")


def _fake_llm(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    _LLM_CALLS.append(task)
    if _POLISH_TASK in task:
        sections = []
        for item in prompt.get("sections", []):
            raw = _TAG_RE.sub("", str(item.get("markdown") or ""))
            markers = "".join(_MARKER_RE.findall(raw))
            lines = [
                line.strip()
                for line in _MARKER_RE.sub(" ", raw).splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            sections.append(
                {
                    "index": item["index"],
                    "markdown": "## {}\n\nRewritten for flow: {} {}".format(
                        item["title"], " ".join(lines), markers
                    ).rstrip(),
                }
            )
        content = json.dumps({"sections": sections})
    else:
        markers = [item["marker"] for item in prompt.get("evidence", [])]
        marker = markers[0] if markers else "S1"
        title = prompt.get("section", {}).get("title", "Section")
        content = json.dumps(
            {
                "markdown": (
                    f"## {title}\n\n"
                    f"The collected evidence supports this section's claim [{marker}]."
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


def _broken_polish_llm(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    prompt = json.loads(body["messages"][-1]["content"])
    task = str(prompt.get("task", ""))
    _LLM_CALLS.append(task)
    if _POLISH_TASK in task:
        content = json.dumps(
            {
                "sections": [
                    {
                        "index": item["index"],
                        "markdown": f"## {item['title']}\n\nRewritten in 2031 [S9].",
                    }
                    for item in prompt.get("sections", [])
                ]
            }
        )
    else:
        return _fake_llm(request)
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [
                {"message": {"role": "assistant", "content": content}},
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


def _settings(**overrides):
    values = {
        "research_plan_approval_required": False,
        "research_outline_approval_required": False,
        "research_llm_writing_enabled": True,
        "research_llm_polish_enabled": True,
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
                title=f"Phase 9 polish workflow {uuid.uuid4().hex[:8]}",
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
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=10.0,
        ),
    )
    return registry


def test_polished_sections_are_written_as_new_versions_and_reach_the_report(database):
    _LLM_CALLS.clear()
    settings = _settings()
    project_id, run_id = _start_run(database)
    registry = _registry(_fake_llm, settings)
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert _LLM_CALLS.count(_POLISH_TASK) == 1

        with database.session() as session:
            sections = list(
                session.scalars(
                    select(DraftSection).where(DraftSection.run_id == run_id)
                )
            )
            events = list(
                session.scalars(select(RunEvent).where(RunEvent.run_id == run_id))
            )
            audits = list(
                session.scalars(
                    select(CitationAudit).where(CitationAudit.run_id == run_id)
                )
            )
            report = session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run_id,
                    Artifact.artifact_type == ArtifactType.RESEARCH_REPORT,
                )
            )

        polished_events = [
            event
            for event in events
            if event.event_type is RunEventType.SECTION_GENERATED
            and event.payload.get("polished") is True
        ]
        assert polished_events, "expected a polished section event"
        summary = [
            event
            for event in events
            if "polish" in (event.message or "").casefold()
            or "fluency" in (event.message or "").casefold()
        ]
        assert summary, "expected an article polish progress event"

        by_index: dict[int, list] = {}
        for section in sections:
            by_index.setdefault(section.section_index, []).append(section)
        assert any(
            len(versions) > 1 for versions in by_index.values()
        ), "polish should have created a new section version"
        for versions in by_index.values():
            latest = max(versions, key=lambda item: item.version)
            if len(versions) > 1:
                assert "Rewritten for flow:" in latest.content_markdown
                first = min(versions, key=lambda item: item.version)
                assert latest.citation_map == first.citation_map
                # verification runs on the polished version, not the draft
                assert latest.id in {audit.draft_section_id for audit in audits}

        report_text = get_file_storage().read(report.storage_key).decode("utf-8")
        assert "Rewritten for flow:" in report_text
        used_markers = {
            match[1:-1] for match in re.findall(r"\[S\d+\]", report_text)
        }
        assert used_markers
        for marker in used_markers:
            assert any(marker in section.citation_map for section in sections), marker
    finally:
        _cleanup(database, project_id)


def test_a_rejected_polish_keeps_the_sections_as_written(database):
    _LLM_CALLS.clear()
    settings = _settings()
    project_id, run_id = _start_run(database)
    registry = _registry(_broken_polish_llm, settings)
    try:
        outcome = run_research_workflow(
            database,
            run_id,
            project_id,
            settings=settings,
            registry=registry,
        )
        assert outcome.status.value == "completed"
        assert _LLM_CALLS.count(_POLISH_TASK) == 1

        with database.session() as session:
            sections = list(
                session.scalars(
                    select(DraftSection).where(DraftSection.run_id == run_id)
                )
            )
            events = list(
                session.scalars(select(RunEvent).where(RunEvent.run_id == run_id))
            )

        assert all(section.version == 1 for section in sections)
        assert not "Rewritten in 2031" in "".join(
            section.content_markdown for section in sections
        )
        kept = [
            event
            for event in events
            if event.event_type is RunEventType.PROGRESS
            and "kept as written" in (event.message or "")
        ]
        assert kept, "expected the skipped polish to be reported"
        assert "citation markers changed" in kept[0].payload["reason"]
    finally:
        _cleanup(database, project_id)
