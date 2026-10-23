from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy.exc import SQLAlchemyError

from dr_core.config import Settings, get_settings
from dr_core.db import Database, get_database
from dr_core.db.enums import RunEventType, RunStatus
from dr_core.db.models import ResearchRun
from dr_core.db.repositories import RunApprovalRepository, RunRepository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.observability import build_tracer, instrument_registry
from dr_core.observability.tracing import ResearchTracer
from dr_core.providers.registry import ProviderRegistry, build_provider_registry
from dr_core.research.context import (
    NodeTimeoutError,
    ResearchContext,
    RunCancelled,
    RunFinished,
)
from dr_core.research.nodes import (
    analyze_evidence,
    analyze_gaps,
    await_outline_approval,
    await_plan_approval,
    extract_evidence,
    fetch_full_text,
    finalize_report,
    generate_outline,
    generate_queries,
    normalize_sources,
    parse_brief,
    parse_documents,
    plan_research,
    polish_article,
    rank_sources,
    research_agent,
    research_recon,
    revise_search_plan,
    route_after_brief,
    route_after_gaps,
    route_after_outline_approval,
    route_after_plan_approval,
    search_sources,
    verify_citations,
    verify_claims,
    write_sections,
)
from dr_core.research.state import ResearchState
from dr_core.services.errors import TransientRunError
from dr_core.services.runs import finalize_cancelled_run
from dr_core.storage import StorageError, get_file_storage

logger = logging.getLogger(__name__)

_checkpoint_setup_lock = threading.Lock()
_checkpoint_setup_done = False
_RECURSION_LIMIT = 80


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    run_id: UUID
    status: RunStatus
    paused_for_approval: bool = False
    approval_id: UUID | None = None


def _traced_node(ctx: ResearchContext, name: str, node):
    def wrapped(state: ResearchState) -> ResearchState:
        with ctx.tracer.span(
            f"node.{name}",
            metadata={
                "node": name,
                "run_id": str(ctx.run_id),
                "round": (state or {}).get("round_index"),
            },
        ) as span:
            result = node(state)
            output: dict = {"current_node": (result or {}).get("current_node")}
            evidence_ids = (result or {}).get("evidence_ids")
            if evidence_ids is not None:
                output["evidence_count"] = len(evidence_ids)
            sections = (result or {}).get("sections")
            if sections is not None:
                output["section_count"] = len(sections)
            span.update(output=output, metadata={"status": "ok"})
            return result

    return wrapped


def build_research_graph(ctx: ResearchContext, checkpointer: PostgresSaver):
    builder = StateGraph(ResearchState)
    builder.add_node(
        "parse_brief",
        _traced_node(ctx, "parse_brief", parse_brief(ctx)),
    )
    builder.add_node(
        "research_agent",
        _traced_node(ctx, "research_agent", research_agent(ctx)),
    )
    builder.add_node(
        "research_recon",
        _traced_node(ctx, "research_recon", research_recon(ctx)),
    )
    builder.add_node(
        "plan_research",
        _traced_node(ctx, "plan_research", plan_research(ctx)),
    )
    builder.add_node(
        "await_plan_approval",
        _traced_node(ctx, "await_plan_approval", await_plan_approval(ctx)),
    )
    builder.add_node(
        "generate_queries",
        _traced_node(ctx, "generate_queries", generate_queries(ctx)),
    )
    builder.add_node(
        "search_sources",
        _traced_node(ctx, "search_sources", search_sources(ctx)),
    )
    builder.add_node(
        "normalize_sources",
        _traced_node(ctx, "normalize_sources", normalize_sources(ctx)),
    )
    builder.add_node(
        "rank_sources",
        _traced_node(ctx, "rank_sources", rank_sources(ctx)),
    )
    builder.add_node(
        "fetch_full_text",
        _traced_node(ctx, "fetch_full_text", fetch_full_text(ctx)),
    )
    builder.add_node(
        "parse_documents",
        _traced_node(ctx, "parse_documents", parse_documents(ctx)),
    )
    builder.add_node(
        "extract_evidence",
        _traced_node(ctx, "extract_evidence", extract_evidence(ctx)),
    )
    builder.add_node(
        "analyze_evidence",
        _traced_node(ctx, "analyze_evidence", analyze_evidence(ctx)),
    )
    builder.add_node(
        "analyze_gaps",
        _traced_node(ctx, "analyze_gaps", analyze_gaps(ctx)),
    )
    builder.add_node(
        "revise_search_plan",
        _traced_node(ctx, "revise_search_plan", revise_search_plan(ctx)),
    )
    builder.add_node(
        "generate_outline",
        _traced_node(ctx, "generate_outline", generate_outline(ctx)),
    )
    builder.add_node(
        "await_outline_approval",
        _traced_node(ctx, "await_outline_approval", await_outline_approval(ctx)),
    )
    builder.add_node(
        "write_sections",
        _traced_node(ctx, "write_sections", write_sections(ctx)),
    )
    builder.add_node(
        "polish_article",
        _traced_node(ctx, "polish_article", polish_article(ctx)),
    )
    builder.add_node(
        "verify_claims",
        _traced_node(ctx, "verify_claims", verify_claims(ctx)),
    )
    builder.add_node(
        "verify_citations",
        _traced_node(ctx, "verify_citations", verify_citations(ctx)),
    )
    builder.add_node(
        "finalize_report",
        _traced_node(ctx, "finalize_report", finalize_report(ctx)),
    )

    builder.add_edge(START, "parse_brief")
    builder.add_conditional_edges(
        "parse_brief",
        route_after_brief(ctx),
        {
            "research_agent": "research_agent",
            "research_recon": "research_recon",
        },
    )
    builder.add_edge("research_agent", "await_plan_approval")
    builder.add_edge("research_recon", "plan_research")
    builder.add_edge("plan_research", "await_plan_approval")
    builder.add_conditional_edges(
        "await_plan_approval",
        route_after_plan_approval,
        {
            "plan_research": "plan_research",
            "generate_queries": "generate_queries",
        },
    )
    builder.add_edge("generate_queries", "search_sources")
    builder.add_edge("search_sources", "normalize_sources")
    builder.add_edge("normalize_sources", "rank_sources")
    builder.add_edge("rank_sources", "fetch_full_text")
    builder.add_edge("fetch_full_text", "parse_documents")
    builder.add_edge("parse_documents", "extract_evidence")
    builder.add_edge("extract_evidence", "analyze_evidence")
    builder.add_edge("analyze_evidence", "analyze_gaps")
    builder.add_conditional_edges(
        "analyze_gaps",
        route_after_gaps,
        {
            "revise_search_plan": "revise_search_plan",
            "generate_outline": "generate_outline",
        },
    )
    builder.add_edge("revise_search_plan", "search_sources")
    builder.add_edge("generate_outline", "await_outline_approval")
    builder.add_conditional_edges(
        "await_outline_approval",
        route_after_outline_approval,
        {
            "generate_outline": "generate_outline",
            "write_sections": "write_sections",
        },
    )
    builder.add_edge("write_sections", "polish_article")
    builder.add_edge("polish_article", "verify_claims")
    builder.add_edge("verify_claims", "verify_citations")
    builder.add_edge("verify_citations", "finalize_report")
    builder.add_edge("finalize_report", END)
    return builder.compile(checkpointer=checkpointer)


def run_research_workflow(
    database: Database | None = None,
    run_id: UUID | None = None,
    project_id: UUID | None = None,
    *,
    resume: dict | None = None,
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    tracer: ResearchTracer | None = None,
    interrupt_after: list[str] | None = None,
) -> WorkflowOutcome:
    if run_id is None or project_id is None:
        raise ValueError("run_id and project_id are required")
    database = database or get_database()
    settings = settings or get_settings()

    with database.session_scope() as session:
        run = session.get(ResearchRun, run_id)
        if run is None or run.project_id != project_id:
            raise EntityNotFound("Research run not found")
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return WorkflowOutcome(run_id=run.id, status=run.status)
        if run.cancel_requested_at is not None:
            finalize_cancelled_run(session, run)
            return WorkflowOutcome(
                run_id=run.id,
                status=RunStatus.CANCELLED,
            )
        if run.status is RunStatus.AWAITING_APPROVAL and resume is None:
            pending = RunApprovalRepository(session).latest(
                run.project_id,
                run.id,
                kind=None,
            )
            return WorkflowOutcome(
                run_id=run.id,
                status=run.status,
                paused_for_approval=True,
                approval_id=pending.id if pending else None,
            )
        if run.status is not RunStatus.AWAITING_APPROVAL:
            resume = None
        thread_id = run.workflow_thread_id

    workflow_registry = registry or _registry_for_mode(settings)
    tracer = tracer or build_tracer(
        run_id=run_id,
        project_id=project_id,
        settings=settings,
    )
    instrument_registry(workflow_registry, tracer)
    tracer.start(
        input_metadata={
            "run_id": str(run_id),
            "project_id": str(project_id),
            "resumed": resume is not None,
        }
    )
    ctx = ResearchContext(
        database=database,
        settings=settings,
        project_id=project_id,
        run_id=run_id,
        registry=workflow_registry,
        storage=get_file_storage(),
        tracer=tracer,
    )
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": _RECURSION_LIMIT,
    }
    trace_error: BaseException | None = None
    try:
        with _postgres_checkpointer(settings) as checkpointer:
            graph = build_research_graph(ctx, checkpointer)
            snapshot = graph.get_state(config)
            if resume is not None:
                graph.invoke(Command(resume=resume), config)
            elif not snapshot.values:
                ctx.ensure_status(
                    RunStatus.PLANNING,
                    event_type=RunEventType.PHASE_STARTED,
                    message="Research planning started",
                    dedupe_key="phase:planning:started",
                )
                graph.invoke(
                    {
                        "limitations": [],
                        "feedback": {},
                        "current_node": "start",
                    },
                    config,
                    interrupt_after=interrupt_after,
                )
            else:
                graph.invoke(None, config)

            snapshot = graph.get_state(config)
            checkpoint_id = (
                snapshot.config.get("configurable", {}).get("checkpoint_id")
                if snapshot
                else None
            )
            if checkpoint_id:
                with database.session_scope() as session:
                    run = session.get(ResearchRun, run_id)
                    if run is not None:
                        RunRepository(session).attach_checkpoint(
                            run_id,
                            str(checkpoint_id),
                            project_id=project_id,
                        )
    except RunCancelled:
        with database.session_scope() as session:
            run = session.get(ResearchRun, run_id)
            if run is not None and run.status is not RunStatus.CANCELLED:
                finalize_cancelled_run(session, run)
    except RunFinished:
        pass
    except NodeTimeoutError as exc:
        trace_error = exc
        raise TransientRunError(str(exc)) from exc
    except (StorageError, SQLAlchemyError) as exc:
        trace_error = exc
        raise TransientRunError(
            f"{type(exc).__name__}: workflow storage or database failure"
        ) from exc
    except Exception as exc:
        trace_error = exc
        raise
    finally:
        workflow_registry.close()
        with database.session() as session:
            run = session.get(ResearchRun, run_id)
            trace_status = run.status.value if run is not None else "unknown"
        if trace_error is not None:
            trace_status = "failed"
        tracer.finish(
            status=trace_status,
            output={"run_id": str(run_id), "project_id": str(project_id)},
            error=trace_error,
        )
        tracer.close()

    with database.session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        approval = None
        if run.status is RunStatus.AWAITING_APPROVAL:
            approval = RunApprovalRepository(session).latest(
                run.project_id,
                run.id,
            )
        return WorkflowOutcome(
            run_id=run.id,
            status=run.status,
            paused_for_approval=run.status is RunStatus.AWAITING_APPROVAL,
            approval_id=approval.id if approval else None,
        )


def _registry_for_mode(settings: Settings) -> ProviderRegistry:
    if settings.research_provider_mode == "mock":
        return build_provider_registry(settings, use_mocks=True)
    if settings.research_provider_mode == "real":
        return build_provider_registry(settings, use_mocks=False)
    registry = build_provider_registry(settings, use_mocks=False)
    if (
        not registry.web_search
        and not registry.academic_search
        and not registry.web_content
        and settings.environment != "production"
    ):
        registry.close()
        logger.info(
            "No external providers configured; using deterministic mock providers"
        )
        return build_provider_registry(settings, use_mocks=True)
    return registry


def _postgres_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


@contextmanager
def _postgres_checkpointer(settings: Settings) -> Iterator[PostgresSaver]:
    global _checkpoint_setup_done
    url = _postgres_url(settings.database_url)
    with PostgresSaver.from_conn_string(url) as checkpointer:
        with _checkpoint_setup_lock:
            if not _checkpoint_setup_done:
                with psycopg.connect(url, autocommit=True) as lock_connection:
                    lock_connection.execute(
                        "SELECT pg_advisory_lock(hashtext('enforge-langgraph-setup'))"
                    )
                    try:
                        checkpointer.setup()
                    finally:
                        lock_connection.execute(
                            "SELECT pg_advisory_unlock(hashtext('enforge-langgraph-setup'))"
                        )
                _checkpoint_setup_done = True
        yield checkpointer


