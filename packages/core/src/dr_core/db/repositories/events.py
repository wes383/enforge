from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from dr_core.db.models import RunEvent
from dr_core.db.repositories.base import Repository
from dr_core.schemas import RunEventCreate


class RunEventRepository(Repository):
    def append(
        self,
        project_id: UUID,
        run_id: UUID,
        data: RunEventCreate,
    ) -> RunEvent:
        self._require_run(run_id, project_id)
        if data.dedupe_key:
            existing = self.find_by_dedupe_key(
                run_id,
                data.dedupe_key,
                project_id=project_id,
            )
            if existing is not None:
                return existing

        event = RunEvent(
            project_id=project_id,
            run_id=run_id,
            event_type=data.event_type,
            stage=data.stage,
            message=data.message,
            payload=data.payload,
            dedupe_key=data.dedupe_key,
        )

        def find_existing() -> RunEvent | None:
            if not data.dedupe_key:
                return None
            return self.find_by_dedupe_key(
                run_id,
                data.dedupe_key,
                project_id=project_id,
            )

        return self._insert_or_get(event, find_existing)

    def find_by_dedupe_key(
        self,
        run_id: UUID,
        dedupe_key: str,
        *,
        project_id: UUID | None = None,
    ) -> RunEvent | None:
        statement = select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.dedupe_key == dedupe_key,
        )
        if project_id is not None:
            statement = statement.where(RunEvent.project_id == project_id)
        return self.session.scalar(statement)

    def list_after(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(RunEvent)
                .where(
                    RunEvent.project_id == project_id,
                    RunEvent.run_id == run_id,
                    RunEvent.id > after_id,
                )
                .order_by(RunEvent.id.asc())
                .limit(min(max(limit, 1), 1000))
            )
        )

    def latest_id(self, project_id: UUID, run_id: UUID) -> int | None:
        self._require_run(run_id, project_id)
        return self.session.scalar(
            select(RunEvent.id)
            .where(
                RunEvent.project_id == project_id,
                RunEvent.run_id == run_id,
            )
            .order_by(RunEvent.id.desc())
            .limit(1)
        )
