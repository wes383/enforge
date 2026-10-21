from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from dr_core.db.enums import ProjectStatus
from dr_core.db.models import ResearchProject
from dr_core.db.repositories.base import Repository
from dr_core.schemas import ProjectCreate, ProjectUpdate

_NON_NULLABLE_PROJECT_FIELDS = {
    "title",
    "topic",
    "language",
    "research_depth",
    "citation_style",
}


class ProjectRepository(Repository):
    def create(self, data: ProjectCreate) -> ResearchProject:
        project = ResearchProject(
            title=data.title,
            topic=data.topic,
            research_scope=data.research_scope,
            language=data.language,
            target_audience=data.target_audience,
            research_depth=data.research_depth,
            citation_style=data.citation_style,
            budget_usd=data.budget_usd,
        )
        return self._flush(project)

    def get(
        self,
        project_id: UUID,
        *,
        include_deleted: bool = False,
    ) -> ResearchProject:
        project = self._require_project(
            project_id,
            include_deleted=include_deleted,
        )
        return project

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[ResearchProject]:
        statement = select(ResearchProject)
        if not include_deleted:
            statement = statement.where(ResearchProject.deleted_at.is_(None))
        statement = (
            statement.order_by(
                ResearchProject.created_at.desc(),
                ResearchProject.id.desc(),
            )
            .offset(offset)
            .limit(min(max(limit, 1), 200))
        )
        return list(self.session.scalars(statement))

    def count(self, *, include_deleted: bool = False) -> int:
        statement = select(func.count()).select_from(ResearchProject)
        if not include_deleted:
            statement = statement.where(ResearchProject.deleted_at.is_(None))
        return int(self.session.scalar(statement) or 0)

    def update(self, project_id: UUID, data: ProjectUpdate) -> ResearchProject:
        project = self.get(project_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is None and field in _NON_NULLABLE_PROJECT_FIELDS:
                continue
            setattr(project, field, value)
        self.session.flush()
        self.session.refresh(project)
        return project

    def soft_delete(self, project_id: UUID) -> ResearchProject:
        project = self.get(project_id)
        if project.deleted_at is None:
            project.deleted_at = datetime.now(tz=UTC)
            project.status = ProjectStatus.ARCHIVED
            self.session.flush()
        return project

    def restore(self, project_id: UUID) -> ResearchProject:
        project = self.get(project_id, include_deleted=True)
        if project.deleted_at is not None:
            project.deleted_at = None
            project.status = ProjectStatus.ACTIVE
            self.session.flush()
        return project
