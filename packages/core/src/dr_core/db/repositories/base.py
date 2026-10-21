from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dr_core.db.models import ResearchProject, ResearchRun
from dr_core.db.repositories.exceptions import EntityNotFound

T = TypeVar("T")


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_project(
        self,
        project_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> ResearchProject:
        project = self.session.get(ResearchProject, project_id)
        if project is None or (not include_deleted and project.deleted_at is not None):
            raise EntityNotFound("Research project not found")
        return project

    def _require_run(self, run_id: UUID, project_id: UUID) -> ResearchRun:
        run = self.session.get(ResearchRun, run_id)
        if run is None or run.project_id != project_id:
            raise EntityNotFound("Research run not found in the project")
        return run

    def _flush(self, entity):
        self.session.add(entity)
        self.session.flush()
        return entity

    def _insert_or_get(self, entity: T, finder: Callable[[], T | None]) -> T:
        try:
            with self.session.begin_nested():
                self.session.add(entity)
                self.session.flush()
        except IntegrityError:
            existing = finder()
            if existing is None:
                raise
            return existing
        return entity
