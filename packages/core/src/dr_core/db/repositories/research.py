from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from dr_core.db.enums import (
    QuestionCoverageStatus,
    SearchQueryStatus,
)
from dr_core.db.models import ResearchQuestion, SearchQuery
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.normalization import question_fingerprint, query_fingerprint
from dr_core.schemas import ResearchQuestionCreate, SearchQueryCreate


class ResearchQuestionRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: ResearchQuestionCreate,
    ) -> ResearchQuestion:
        self._require_run(run_id, project_id)
        if data.parent_id is not None:
            parent = self.session.get(ResearchQuestion, data.parent_id)
            if (
                parent is None
                or parent.project_id != project_id
                or parent.run_id != run_id
            ):
                raise EntityNotFound("Parent research question not found in the run")

        fingerprint = question_fingerprint(data.question)
        existing = self.session.scalar(
            select(ResearchQuestion).where(
                ResearchQuestion.run_id == run_id,
                ResearchQuestion.question_hash == fingerprint,
            )
        )
        if existing is not None:
            return existing

        question = ResearchQuestion(
            project_id=project_id,
            run_id=run_id,
            parent_id=data.parent_id,
            question=data.question,
            question_hash=fingerprint,
            priority=data.priority,
        )
        return self._insert_or_get(
            question,
            lambda: self.session.scalar(
                select(ResearchQuestion).where(
                    ResearchQuestion.run_id == run_id,
                    ResearchQuestion.question_hash == fingerprint,
                )
            ),
        )

    def create_many(
        self,
        project_id: UUID,
        run_id: UUID,
        items: list[ResearchQuestionCreate],
    ) -> list[ResearchQuestion]:
        return [self.create(project_id, run_id, item) for item in items]

    def list_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[ResearchQuestion]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(ResearchQuestion)
                .where(
                    ResearchQuestion.project_id == project_id,
                    ResearchQuestion.run_id == run_id,
                )
                .order_by(
                    ResearchQuestion.priority.desc(),
                    ResearchQuestion.created_at.asc(),
                )
            )
        )

    def update_coverage(
        self,
        question_id: UUID,
        coverage_status: QuestionCoverageStatus,
        *,
        project_id: UUID,
    ) -> ResearchQuestion:
        question = self.session.get(ResearchQuestion, question_id)
        if question is None or question.project_id != project_id:
            raise EntityNotFound("Research question not found")
        question.coverage_status = coverage_status
        self.session.flush()
        return question


class SearchQueryRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: SearchQueryCreate,
    ) -> SearchQuery:
        self._require_run(run_id, project_id)
        if data.research_question_id is not None:
            question = self.session.get(ResearchQuestion, data.research_question_id)
            if (
                question is None
                or question.project_id != project_id
                or question.run_id != run_id
            ):
                raise EntityNotFound("Research question not found in the run")

        fingerprint = query_fingerprint(data.query_text)
        existing = self.session.scalar(
            select(SearchQuery).where(
                SearchQuery.run_id == run_id,
                SearchQuery.round_index == data.round_index,
                SearchQuery.provider == data.provider,
                SearchQuery.query_hash == fingerprint,
            )
        )
        if existing is not None:
            return existing

        query = SearchQuery(
            project_id=project_id,
            run_id=run_id,
            research_question_id=data.research_question_id,
            query_text=data.query_text,
            query_hash=fingerprint,
            provider=data.provider,
            round_index=data.round_index,
        )
        return self._insert_or_get(
            query,
            lambda: self.session.scalar(
                select(SearchQuery).where(
                    SearchQuery.run_id == run_id,
                    SearchQuery.round_index == data.round_index,
                    SearchQuery.provider == data.provider,
                    SearchQuery.query_hash == fingerprint,
                )
            ),
        )

    def list_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[SearchQuery]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(SearchQuery)
                .where(
                    SearchQuery.project_id == project_id,
                    SearchQuery.run_id == run_id,
                )
                .order_by(
                    SearchQuery.round_index.asc(),
                    SearchQuery.created_at.asc(),
                )
            )
        )

    def mark_running(
        self,
        query_id: UUID,
        *,
        project_id: UUID,
    ) -> SearchQuery:
        query = self._get(query_id, project_id)
        query.status = SearchQueryStatus.RUNNING
        self.session.flush()
        return query

    def mark_completed(
        self,
        query_id: UUID,
        *,
        project_id: UUID,
        result_count: int,
    ) -> SearchQuery:
        query = self._get(query_id, project_id)
        query.status = SearchQueryStatus.COMPLETED
        query.result_count = result_count
        query.executed_at = datetime.now(tz=UTC)
        query.error_summary = None
        self.session.flush()
        return query

    def mark_failed(
        self,
        query_id: UUID,
        *,
        project_id: UUID,
        error_summary: str,
    ) -> SearchQuery:
        query = self._get(query_id, project_id)
        query.status = SearchQueryStatus.FAILED
        query.executed_at = datetime.now(tz=UTC)
        query.error_summary = error_summary
        self.session.flush()
        return query

    def _get(self, query_id: UUID, project_id: UUID) -> SearchQuery:
        query = self.session.get(SearchQuery, query_id)
        if query is None or query.project_id != project_id:
            raise EntityNotFound("Search query not found")
        return query
