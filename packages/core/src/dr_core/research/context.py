from __future__ import annotations

import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from dr_core.config import Settings
from dr_core.db import Database
from dr_core.db.enums import RunEventType, RunStatus
from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.db.repositories import RunEventRepository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.observability.tracing import ResearchTracer
from dr_core.providers.registry import ProviderRegistry
from dr_core.schemas import RunEventCreate
from dr_core.services.runs import finalize_cancelled_run, lock_run, transition_run
from dr_core.storage import FileStorage


class RunCancelled(Exception):
    pass


class RunFinished(Exception):
    pass


class NodeTimeoutError(TimeoutError):
    pass


@dataclass(slots=True)
class ResearchContext:
    database: Database
    settings: Settings
    project_id: UUID
    run_id: UUID
    registry: ProviderRegistry
    storage: FileStorage
    tracer: ResearchTracer

    def load_run(self, session) -> ResearchRun:
        run = session.get(ResearchRun, self.run_id)
        if run is None or run.project_id != self.project_id:
            raise EntityNotFound("Research run not found")
        return run

    def guard(self) -> None:
        with self.database.session() as session:
            run = self.load_run(session)
            if run.cancel_requested_at is not None:
                if run.status is not RunStatus.CANCELLED:
                    finalize_cancelled_run(session, run)
                    session.commit()
                raise RunCancelled("Research run was cancelled")
            if run.status in {
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.COMPLETED,
            }:
                raise RunFinished(f"Research run is already {run.status.value}")

    def ensure_status(
        self,
        target: RunStatus,
        *,
        event_type: RunEventType = RunEventType.PHASE_STARTED,
        message: str | None = None,
        payload: dict | None = None,
        dedupe_key: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        with self.database.session_scope() as session:
            run = lock_run(session, self.run_id, self.project_id)
            if run is None:
                raise EntityNotFound("Research run not found")
            if run.cancel_requested_at is not None:
                finalize_cancelled_run(session, run)
                raise RunCancelled("Research run was cancelled")
            if run.status is target:
                return
            if run.status in {
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.COMPLETED,
            }:
                raise RunFinished(f"Research run is already {run.status.value}")
            transition_run(
                session,
                run,
                target,
                event_type=event_type,
                message=message,
                payload=payload,
                dedupe_key=dedupe_key,
                error_summary=error_summary,
            )

    def emit(
        self,
        event_type: RunEventType,
        *,
        message: str | None = None,
        payload: dict | None = None,
        dedupe_key: str | None = None,
        stage: RunStatus | None = None,
    ) -> None:
        with self.database.session_scope() as session:
            run = self.load_run(session)
            RunEventRepository(session).append(
                run.project_id,
                run.id,
                RunEventCreate(
                    event_type=event_type,
                    stage=stage or run.status,
                    message=message,
                    payload=payload or {},
                    dedupe_key=dedupe_key,
                ),
            )

    def run_budget(self, run: ResearchRun | None = None) -> dict[str, Any]:
        with self.database.session() as session:
            current_run = run or self.load_run(session)
            project = session.get(ResearchProject, self.project_id)
            if project is None or project.deleted_at is not None:
                raise EntityNotFound("Research project not found")
            return _budget_values(
                current_run,
                self.settings,
                project.budget_usd,
            )

    def project(self) -> ResearchProject:
        with self.database.session() as session:
            project = session.get(ResearchProject, self.project_id)
            if project is None or project.deleted_at is not None:
                raise EntityNotFound("Research project not found")
            session.expunge(project)
            return project


def _budget_values(
    run: ResearchRun,
    settings: Settings,
    project_budget: Decimal | None,
) -> dict[str, Any]:
    token_budget = settings.research_token_budget
    configured_cost = Decimal(str(settings.research_cost_budget_usd))
    cost_budget = _minimum_positive(configured_cost, project_budget)
    usage_tokens = int(run.input_tokens or 0) + int(run.output_tokens or 0)
    usage_cost = Decimal(run.cost_usd or 0)
    return {
        "usage_tokens": usage_tokens,
        "token_budget": token_budget,
        "token_exhausted": token_budget > 0 and usage_tokens >= token_budget,
        "usage_cost": usage_cost,
        "cost_budget": cost_budget,
        "cost_exhausted": cost_budget > 0 and usage_cost >= cost_budget,
    }


def _minimum_positive(*values: Decimal | None) -> Decimal:
    positive = [value for value in values if value is not None and value > 0]
    return min(positive) if positive else Decimal("0")


@contextmanager
def node_timeout(seconds: int) -> Iterator[None]:
    """Bound a workflow node on the worker's main thread.

    Provider clients already enforce connect/read timeouts; this catches
    accidental CPU or database loops as well. Signal-based timeouts are only
    available on the main thread of a Unix process.
    """
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def _raise(signum, frame) -> None:
        raise NodeTimeoutError(f"Workflow node exceeded {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
