from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dr_core.db.enums import TERMINAL_RUN_STATUSES, RunStatus
from dr_core.db.models import ResearchRun
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.schemas import RunCreate


class RunRepository(Repository):
    def create(self, project_id: UUID, data: RunCreate) -> ResearchRun:
        run, _ = self.create_or_get(project_id, data)
        return run

    def create_or_get(
        self,
        project_id: UUID,
        data: RunCreate,
    ) -> tuple[ResearchRun, bool]:
        self._require_project(project_id)
        if data.idempotency_key:
            existing = self.find_by_idempotency_key(
                project_id,
                data.idempotency_key,
            )
            if existing is not None:
                return existing, False

        run = ResearchRun(
            project_id=project_id,
            idempotency_key=data.idempotency_key,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
            return run, True
        except IntegrityError:
            if data.idempotency_key:
                existing = self.find_by_idempotency_key(
                    project_id,
                    data.idempotency_key,
                )
                if existing is not None:
                    return existing, False
            raise

    def find_by_idempotency_key(
        self,
        project_id: UUID,
        idempotency_key: str,
    ) -> ResearchRun | None:
        return self.session.scalar(
            select(ResearchRun).where(
                ResearchRun.project_id == project_id,
                ResearchRun.idempotency_key == idempotency_key,
            )
        )

    def get(self, run_id: UUID, *, project_id: UUID | None = None) -> ResearchRun:
        run = self.session.get(ResearchRun, run_id)
        if run is None or (project_id is not None and run.project_id != project_id):
            raise EntityNotFound("Research run not found")
        return run

    def list_for_project(
        self,
        project_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ResearchRun]:
        self._require_project(project_id)
        return list(
            self.session.scalars(
                select(ResearchRun)
                .where(ResearchRun.project_id == project_id)
                .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
                .offset(offset)
                .limit(min(max(limit, 1), 200))
            )
        )

    def count_for_project(self, project_id: UUID) -> int:
        self._require_project(project_id)
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.project_id == project_id)
            )
            or 0
        )

    def count_active_for_project(self, project_id: UUID) -> int:
        self._require_project(project_id)
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(
                    ResearchRun.project_id == project_id,
                    ~ResearchRun.status.in_(list(TERMINAL_RUN_STATUSES)),
                )
            )
            or 0
        )

    def update_status(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        project_id: UUID | None = None,
        error_summary: str | None = None,
    ) -> ResearchRun:
        run = self.get(run_id, project_id=project_id)
        now = datetime.now(tz=UTC)
        if status != RunStatus.QUEUED and run.started_at is None:
            run.started_at = now
        run.status = status
        if status in TERMINAL_RUN_STATUSES:
            run.finished_at = now
        if error_summary is not None:
            run.error_summary = error_summary
        self.session.flush()
        return run

    def attach_checkpoint(
        self,
        run_id: UUID,
        checkpoint_id: str,
        *,
        project_id: UUID | None = None,
    ) -> ResearchRun:
        run = self.get(run_id, project_id=project_id)
        run.checkpoint_id = checkpoint_id
        self.session.flush()
        return run
