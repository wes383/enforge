from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text

from dr_core.config import Settings, get_settings
from dr_core.db import Database, get_database
from dr_core.db.models import (
    Artifact,
    ResearchProject,
    ResearchRun,
    SourceVersion,
)
from dr_core.db.repositories import ProjectRepository, RunRepository
from dr_core.evaluation.cases import CASES, DATASET_VERSION, EvaluationCase
from dr_core.evaluation.metrics import (
    CaseMetrics,
    aggregate_metrics,
    case_metrics_dict,
    collect_case_metrics,
    compare_human_review,
    human_review_sample,
)
from dr_core.evaluation.providers import build_case_registry
from dr_core.observability.tracing import ResearchTracer
from dr_core.research.graph import run_research_workflow
from dr_core.schemas import ProjectCreate, RunCreate
from dr_core.storage import StorageError, StorageFileNotFound, get_file_storage


@dataclass(slots=True)
class CaseResult:
    id: str
    category: str
    scenario: str
    project_id: str
    run_id: str | None
    status: str
    duration_seconds: float
    checks: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    error: str | None = None
    human_review_sample: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "completed" and all(
            check["passed"] for check in self.checks
        )


@dataclass(frozen=True, slots=True)
class EvaluationOptions:
    keep_data: bool = False
    include_human_sample: bool = True
    human_review_limit: int = 20


def evaluation_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "research_plan_approval_required": False,
            "research_outline_approval_required": False,
            "research_provider_mode": "mock",
            "research_max_search_rounds": 3,
            "research_max_queries_per_round": 6,
            "research_max_sources": 20,
            "research_max_fetches_per_round": 8,
            "research_max_evidence": 40,
            "research_node_timeout_seconds": 60,
            "langfuse_enabled": False,
        }
    )


def run_evaluation(
    *,
    case_ids: list[str] | None = None,
    options: EvaluationOptions | None = None,
    database: Database | None = None,
    settings: Settings | None = None,
    reviewed_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    options = options or EvaluationOptions()
    database = database or get_database()
    settings = settings or evaluation_settings()
    storage = get_file_storage()
    selected = [
        case for case in CASES if case_ids is None or case.id in set(case_ids)
    ]
    if not selected:
        raise ValueError("No evaluation cases selected")
    results: list[CaseResult] = []
    for case in selected:
        results.append(
            _run_case(
                case,
                database=database,
                settings=settings,
                options=options,
            )
        )
    summary = aggregate_metrics(
        CaseMetrics(**result.metrics)
        for result in results
        if result.metrics is not None
    )
    successful = [result for result in results if result.passed]
    summary["task_success_rate"] = _ratio(
        sum(1 for result in results if result.status == "completed"),
        len(results),
    )
    recovery_cases = [
        result for result in results if result.scenario == "worker_interrupt"
    ]
    recovery_checks = [
        check
        for result in recovery_cases
        for check in result.checks
        if check["name"] == "resume_after_interrupt"
    ]
    summary["recovery_success_rate"] = _ratio(
        sum(1 for check in recovery_checks if check["passed"]),
        len(recovery_checks),
    )
    summary["assertion_pass_rate"] = _ratio(
        sum(1 for result in successful),
        len(results),
    )
    sample = [
        entry
        for result in results
        for entry in result.human_review_sample
    ]
    report: dict[str, Any] = {
        "dataset": DATASET_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "provider_mode": "mock",
        "cases": [
            {
                **asdict(result),
                "passed": result.passed,
            }
            for result in results
        ],
        "summary": summary,
        "human_review_sample": sample,
        "methodology": {
            "citation_validity": (
                "Passed section citation audits divided by all citation audits."
            ),
            "citation_support_accuracy": (
                "Claims with evidence links that verify as SUPPORTED divided by "
                "all claims with evidence links. Deterministic rules, not an LLM "
                "judge."
            ),
            "question_coverage": (
                "Research questions marked covered divided by all questions."
            ),
            "unsupported_claim_ratio": (
                "Claims not verified as SUPPORTED divided by all claims."
            ),
            "metadata_accuracy": (
                "Sources with title, authors, date, venue/publisher, and DOI or "
                "URL divided by all sources."
            ),
            "recovery_success": (
                "A workflow interrupted after rank_sources resumes from the "
                "PostgreSQL checkpoint and completes."
            ),
        },
    }
    if reviewed_claims is not None:
        report["human_review"] = compare_human_review(sample, reviewed_claims)
    else:
        report["human_review"] = None
    return report


def _run_case(
    case: EvaluationCase,
    *,
    database: Database,
    settings: Settings,
    options: EvaluationOptions,
) -> CaseResult:
    started = time.monotonic()
    project_id: UUID
    run_id: UUID
    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            ProjectCreate(
                title=f"Phase 9 evaluation {case.id}",
                topic=case.topic,
                language=case.language,
                research_depth=case.depth,
                budget_usd=case.budget_usd,
            )
        )
        run = RunRepository(session).create(project.id, RunCreate())
        if case.budget_usd is not None:
            run.cost_usd = Decimal("0.02")
        project_id = project.id
        run_id = run.id
        session.refresh(run)

    error: str | None = None
    recovered = False
    interrupted = False
    events: list[dict[str, Any]] = []
    try:
        if case.scenario == "worker_interrupt":
            first = _invoke(
                case,
                run_id,
                project_id,
                database,
                settings,
                interrupt_after=["rank_sources"],
            )
            events.extend(first["events"])
            interrupted = first["outcome"].status.value != "completed"
            resumed = _invoke(
                case,
                run_id,
                project_id,
                database,
                settings,
            )
            events.extend(resumed["events"])
            recovered = (
                interrupted
                and resumed["outcome"].status.value == "completed"
            )
        else:
            invocation = _invoke(
                case,
                run_id,
                project_id,
                database,
                settings,
            )
            events.extend(invocation["events"])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]

    duration = round(time.monotonic() - started, 3)
    with database.session() as session:
        run = session.get(ResearchRun, run_id)
        metrics = collect_case_metrics(session, get_file_storage(), run) if run else None
        sample = (
            human_review_sample(
                session,
                run_id,
                limit=options.human_review_limit,
            )
            if run and options.include_human_sample
            else []
        )
    checks = _checks(
        case,
        metrics,
        recovered=recovered,
        interrupted=interrupted,
    )
    result = CaseResult(
        id=case.id,
        category=case.category,
        scenario=case.scenario,
        project_id=str(project_id),
        run_id=str(run_id),
        status=metrics.status if metrics is not None else "failed",
        duration_seconds=duration,
        checks=checks,
        metrics=case_metrics_dict(metrics) if metrics is not None else None,
        trace=_trace_summary(events),
        error=error,
        human_review_sample=sample,
    )
    if not options.keep_data:
        _cleanup_project(database, project_id)
    return result


def _invoke(
    case: EvaluationCase,
    run_id: UUID,
    project_id: UUID,
    database: Database,
    settings: Settings,
    *,
    interrupt_after: list[str] | None = None,
) -> dict[str, Any]:
    tracer = ResearchTracer(
        run_id=run_id,
        project_id=project_id,
        environment=settings.environment,
        app_version=settings.app_version,
        capture=True,
        include_content=False,
    )
    registry = build_case_registry(case, settings)
    outcome = run_research_workflow(
        database,
        run_id,
        project_id,
        settings=settings,
        registry=registry,
        tracer=tracer,
        interrupt_after=interrupt_after,
    )
    return {"outcome": outcome, "events": tracer.events}


def _checks(
    case: EvaluationCase,
    metrics: CaseMetrics | None,
    *,
    recovered: bool,
    interrupted: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [
        {
            "name": "completed",
            "passed": metrics is not None and metrics.status == "completed",
            "detail": metrics.status if metrics is not None else "no metrics",
        }
    ]
    if metrics is None:
        return checks
    checks.append(
        _check("has_evidence", metrics.evidence_count > 0, metrics.evidence_count)
    )
    expected = case.expected
    if "refute_evidence" in expected:
        checks.append(
            _check(
                "has_refute_evidence",
                (metrics.refute_evidence_count > 0)
                is bool(expected["refute_evidence"]),
                metrics.refute_evidence_count,
            )
        )
    if "min_refute_evidence" in expected:
        checks.append(
            _check(
                "has_refute_evidence",
                metrics.refute_evidence_count >= int(expected["min_refute_evidence"]),
                metrics.refute_evidence_count,
            )
        )
    if "min_snippet_evidence" in expected:
        checks.append(
            _check(
                "has_snippet_evidence",
                metrics.snippet_evidence_count
                >= int(expected["min_snippet_evidence"]),
                metrics.snippet_evidence_count,
            )
        )
    if "min_full_text_evidence" in expected:
        checks.append(
            _check(
                "has_full_text_evidence",
                metrics.full_text_evidence_count
                >= int(expected["min_full_text_evidence"]),
                metrics.full_text_evidence_count,
            )
        )
    if "metadata_accuracy_below" in expected:
        accuracy = metrics.metadata_accuracy_rate
        checks.append(
            _check(
                "metadata_accuracy_below_threshold",
                accuracy is not None
                and accuracy < float(expected["metadata_accuracy_below"]),
                accuracy,
            )
        )
    if "min_failed_queries" in expected:
        checks.append(
            _check(
                "rate_limit_recorded",
                metrics.failed_query_count >= int(expected["min_failed_queries"]),
                metrics.failed_query_count,
            )
        )
    if "report_mentions_budget" in expected:
        checks.append(
            _check(
                "budget_limitation_reported",
                metrics.report_mentions_budget
                is bool(expected["report_mentions_budget"]),
                metrics.report_mentions_budget,
            )
        )
    if "min_academic_sources" in expected:
        checks.append(
            _check(
                "academic_sources_ingested",
                metrics.academic_source_count
                >= int(expected["min_academic_sources"]),
                metrics.academic_source_count,
            )
        )
    if expected.get("must_resume"):
        checks.append(
            _check("interrupt_observed", interrupted, interrupted)
        )
        checks.append(
            _check("resume_after_interrupt", recovered, recovered)
        )
    return checks


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _trace_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_count": len(events),
        "span_count": sum(
            1 for event in events if event["type"] == "span-create"
        ),
        "generation_count": sum(
            1 for event in events if event["type"] == "generation-create"
        ),
        "error_spans": sum(
            1
            for event in events
            if event["type"] == "span-update"
            and event["body"].get("level") == "ERROR"
        ),
    }


def _cleanup_project(database: Database, project_id: UUID) -> None:
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
    for storage_key in storage_keys:
        if not storage_key:
            continue
        try:
            storage.delete(storage_key)
        except (StorageFileNotFound, StorageError):
            pass


def write_report(report: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)


def load_human_review(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Human review file must contain a JSON list")
    return payload


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
