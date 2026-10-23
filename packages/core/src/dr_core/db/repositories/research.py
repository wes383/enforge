from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from dr_core.db.enums import SearchQueryStatus, SectionCoverageStatus
from dr_core.db.models import OutlineSectionRecord, SearchQuery
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.normalization import query_fingerprint, section_fingerprint
from dr_core.schemas import OutlineSectionCreate, SearchQueryCreate


class OutlineSectionRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: OutlineSectionCreate,
    ) -> OutlineSectionRecord:
        self._require_run(run_id, project_id)
        fingerprint = section_fingerprint(data.title)
        existing = self.session.scalar(
            select(OutlineSectionRecord).where(
                OutlineSectionRecord.run_id == run_id,
                OutlineSectionRecord.section_hash == fingerprint,
            )
        )
        if existing is not None:
            existing.title = data.title
            existing.role = data.role
            existing.purpose = data.purpose
            existing.search_terms = list(data.search_terms)
            existing.target_words = data.target_words
            existing.priority = data.priority
            self.session.flush()
            return existing

        section = OutlineSectionRecord(
            id=data.id,
            project_id=project_id,
            run_id=run_id,
            title=data.title,
            section_hash=fingerprint,
            role=data.role,
            purpose=data.purpose,
            search_terms=list(data.search_terms),
            target_words=data.target_words,
            priority=data.priority,
        )
        return self._insert_or_get(
            section,
            lambda: self.session.scalar(
                select(OutlineSectionRecord).where(
                    OutlineSectionRecord.run_id == run_id,
                    OutlineSectionRecord.section_hash == fingerprint,
                )
            ),
        )

    def create_many(
        self,
        project_id: UUID,
        run_id: UUID,
        items: list[OutlineSectionCreate],
    ) -> list[OutlineSectionRecord]:
        return [self.create(project_id, run_id, item) for item in items]

    def sync(
        self,
        project_id: UUID,
        run_id: UUID,
        items: list[OutlineSectionCreate],
    ) -> list[OutlineSectionRecord]:
        """Make the run's sections match an approved outline exactly."""
        self._require_run(run_id, project_id)
        existing = list(
            self.session.scalars(
                select(OutlineSectionRecord).where(
                    OutlineSectionRecord.project_id == project_id,
                    OutlineSectionRecord.run_id == run_id,
                )
            )
        )
        by_id = {record.id: record for record in existing}
        by_hash = {record.section_hash: record for record in existing}
        kept: list[OutlineSectionRecord] = []
        for data in items:
            record = by_id.get(data.id) if data.id is not None else None
            if record is None:
                record = by_hash.get(section_fingerprint(data.title))
            if record is None:
                record = OutlineSectionRecord(
                    id=data.id or uuid4(),
                    project_id=project_id,
                    run_id=run_id,
                    title=data.title,
                    section_hash=f"pending:{uuid4().hex}",
                    role=data.role,
                    purpose=data.purpose,
                    search_terms=list(data.search_terms),
                    target_words=data.target_words,
                    priority=data.priority,
                )
                self.session.add(record)
            kept.append(record)

        kept_ids = {record.id for record in kept}
        stale_ids = [record.id for record in existing if record.id not in kept_ids]
        if stale_ids:
            self.session.execute(
                delete(OutlineSectionRecord).where(
                    OutlineSectionRecord.id.in_(stale_ids)
                )
            )
            self.session.flush()

        # Hashes are unique per run, so move them aside before re-labelling.
        for record in kept:
            record.section_hash = f"pending:{uuid4().hex}"
        self.session.flush()
        for record, data in zip(kept, items):
            record.title = data.title
            record.section_hash = section_fingerprint(data.title)
            record.role = data.role
            record.purpose = data.purpose
            record.search_terms = list(data.search_terms)
            record.target_words = data.target_words
            record.priority = data.priority
        self.session.flush()
        return kept

    def list_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[OutlineSectionRecord]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(OutlineSectionRecord)
                .where(
                    OutlineSectionRecord.project_id == project_id,
                    OutlineSectionRecord.run_id == run_id,
                )
                .order_by(
                    OutlineSectionRecord.priority.desc(),
                    OutlineSectionRecord.created_at.asc(),
                )
            )
        )

    def update_coverage(
        self,
        section_id: UUID,
        coverage_status: SectionCoverageStatus,
        *,
        project_id: UUID,
    ) -> OutlineSectionRecord:
        section = self.session.get(OutlineSectionRecord, section_id)
        if section is None or section.project_id != project_id:
            raise EntityNotFound("Outline section not found")
        section.coverage_status = coverage_status
        self.session.flush()
        return section


class SearchQueryRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: SearchQueryCreate,
    ) -> SearchQuery:
        self._require_run(run_id, project_id)
        if data.outline_section_id is not None:
            section = self.session.get(
                OutlineSectionRecord,
                data.outline_section_id,
            )
            if (
                section is None
                or section.project_id != project_id
                or section.run_id != run_id
            ):
                raise EntityNotFound("Outline section not found in the run")

        fingerprint = query_fingerprint(data.query_text)
        existing = self.session.scalar(
            select(SearchQuery).where(
                SearchQuery.run_id == run_id,
                SearchQuery.round_index == data.round_index,
                SearchQuery.provider == data.provider,
                SearchQuery.outline_section_id == data.outline_section_id,
                SearchQuery.query_hash == fingerprint,
            )
        )
        if existing is not None:
            return existing

        query = SearchQuery(
            project_id=project_id,
            run_id=run_id,
            outline_section_id=data.outline_section_id,
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
                    SearchQuery.outline_section_id == data.outline_section_id,
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
