from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update

from dr_core.db.enums import (
    DraftSectionStatus,
    OutlineStatus,
)
from dr_core.db.models import (
    CitationAudit,
    Claim,
    DraftSection,
    Outline,
)
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.normalization import text_sha256
from dr_core.schemas import (
    CitationAuditCreate,
    DraftSectionCreate,
    OutlineCreate,
)


class OutlineRepository(Repository):
    def create_version(
        self,
        project_id: UUID,
        run_id: UUID,
        data: OutlineCreate,
    ) -> Outline:
        self._require_run(run_id, project_id)
        latest_version = self.session.scalar(
            select(func.coalesce(func.max(Outline.version), 0)).where(
                Outline.run_id == run_id
            )
        )
        version = int(latest_version or 0) + 1
        outline = Outline(
            project_id=project_id,
            run_id=run_id,
            version=version,
            content=data.content,
            version_note=data.version_note,
        )
        return self._insert_or_get(
            outline,
            lambda: self.find_by_version(project_id, run_id, version),
        )

    def find_by_version(
        self,
        project_id: UUID,
        run_id: UUID,
        version: int,
    ) -> Outline | None:
        return self.session.scalar(
            select(Outline).where(
                Outline.project_id == project_id,
                Outline.run_id == run_id,
                Outline.version == version,
            )
        )

    def get(self, outline_id: UUID, *, project_id: UUID) -> Outline:
        outline = self.session.get(Outline, outline_id)
        if outline is None or outline.project_id != project_id:
            raise EntityNotFound("Outline not found")
        return outline

    def latest(self, project_id: UUID, run_id: UUID) -> Outline | None:
        self._require_run(run_id, project_id)
        return self.session.scalar(
            select(Outline)
            .where(
                Outline.project_id == project_id,
                Outline.run_id == run_id,
            )
            .order_by(Outline.version.desc())
            .limit(1)
        )

    def list_for_run(self, project_id: UUID, run_id: UUID) -> list[Outline]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(Outline)
                .where(
                    Outline.project_id == project_id,
                    Outline.run_id == run_id,
                )
                .order_by(Outline.version.desc())
            )
        )

    def approve(self, outline_id: UUID, *, project_id: UUID) -> Outline:
        outline = self.get(outline_id, project_id=project_id)
        self.session.execute(
            update(Outline)
            .where(
                Outline.run_id == outline.run_id,
                Outline.status == OutlineStatus.APPROVED,
                Outline.id != outline.id,
            )
            .values(status=OutlineStatus.SUPERSEDED)
        )
        outline.status = OutlineStatus.APPROVED
        outline.approved_at = datetime.now(tz=UTC)
        self.session.flush()
        return outline

    def reject(self, outline_id: UUID, *, project_id: UUID) -> Outline:
        outline = self.get(outline_id, project_id=project_id)
        outline.status = OutlineStatus.REJECTED
        outline.approved_at = None
        self.session.flush()
        return outline

    def update_section_statuses(
        self,
        outline_id: UUID,
        statuses: dict[int, str],
        *,
        project_id: UUID,
    ) -> Outline:
        outline = self.get(outline_id, project_id=project_id)
        content = dict(outline.content)
        sections = [dict(item) for item in content.get("sections", [])]
        for item in sections:
            status = statuses.get(int(item.get("index", -1)))
            if status is not None:
                item["status"] = status
        content["sections"] = sections
        outline.content = content
        self.session.flush()
        return outline


class DraftSectionRepository(Repository):
    def create_version(
        self,
        project_id: UUID,
        run_id: UUID,
        data: DraftSectionCreate,
    ) -> DraftSection:
        self._require_run(run_id, project_id)
        if data.outline_id is not None:
            outline = self.session.get(Outline, data.outline_id)
            if (
                outline is None
                or outline.project_id != project_id
                or outline.run_id != run_id
            ):
                raise EntityNotFound("Outline not found in the run")

        latest_version = self.session.scalar(
            select(func.coalesce(func.max(DraftSection.version), 0)).where(
                DraftSection.run_id == run_id,
                DraftSection.section_index == data.section_index,
            )
        )
        version = int(latest_version or 0) + 1
        section = DraftSection(
            project_id=project_id,
            run_id=run_id,
            outline_id=data.outline_id,
            section_index=data.section_index,
            title=data.title,
            content_markdown=data.content_markdown,
            citation_map=data.citation_map,
            evidence_ids=[str(item) for item in data.evidence_ids],
            version=version,
            generation_status=data.generation_status,
            content_hash=(
                text_sha256(data.content_markdown) if data.content_markdown else None
            ),
        )
        return self._insert_or_get(
            section,
            lambda: self.find_by_version(
                project_id,
                run_id,
                data.section_index,
                version,
            ),
        )

    def find_by_version(
        self,
        project_id: UUID,
        run_id: UUID,
        section_index: int,
        version: int,
    ) -> DraftSection | None:
        return self.session.scalar(
            select(DraftSection).where(
                DraftSection.project_id == project_id,
                DraftSection.run_id == run_id,
                DraftSection.section_index == section_index,
                DraftSection.version == version,
            )
        )

    def get(self, section_id: UUID, *, project_id: UUID) -> DraftSection:
        section = self.session.get(DraftSection, section_id)
        if section is None or section.project_id != project_id:
            raise EntityNotFound("Draft section not found")
        return section

    def list_for_run(self, project_id: UUID, run_id: UUID) -> list[DraftSection]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(DraftSection)
                .where(
                    DraftSection.project_id == project_id,
                    DraftSection.run_id == run_id,
                )
                .order_by(
                    DraftSection.section_index.asc(),
                    DraftSection.version.desc(),
                )
            )
        )

    def list_latest_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[DraftSection]:
        latest: dict[int, DraftSection] = {}
        for section in self.list_for_run(project_id, run_id):
            current = latest.get(section.section_index)
            if current is None or section.version > current.version:
                latest[section.section_index] = section
        return [latest[index] for index in sorted(latest)]

    def get_latest(
        self,
        project_id: UUID,
        run_id: UUID,
        section_index: int,
    ) -> DraftSection | None:
        return self.session.scalar(
            select(DraftSection)
            .where(
                DraftSection.project_id == project_id,
                DraftSection.run_id == run_id,
                DraftSection.section_index == section_index,
            )
            .order_by(DraftSection.version.desc())
            .limit(1)
        )

    def update_status(
        self,
        section_id: UUID,
        status: DraftSectionStatus,
        *,
        project_id: UUID,
    ) -> DraftSection:
        section = self.get(section_id, project_id=project_id)
        section.generation_status = status
        self.session.flush()
        return section


class CitationAuditRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: CitationAuditCreate,
    ) -> CitationAudit:
        self._require_run(run_id, project_id)
        section = self.session.get(DraftSection, data.draft_section_id)
        if (
            section is None
            or section.project_id != project_id
            or section.run_id != run_id
        ):
            raise EntityNotFound("Draft section not found in the run")
        if data.claim_id is not None:
            claim = self.session.get(Claim, data.claim_id)
            if (
                claim is None
                or claim.project_id != project_id
                or claim.run_id != run_id
            ):
                raise EntityNotFound("Claim not found in the run")

        audit = CitationAudit(
            project_id=project_id,
            run_id=run_id,
            draft_section_id=data.draft_section_id,
            claim_id=data.claim_id,
            citation_marker=data.citation_marker,
            status=data.status,
            issue_type=data.issue_type,
            details=data.details,
            audit_method=data.audit_method,
            correction_note=data.correction_note,
            corrected_in_version=data.corrected_in_version,
        )
        return self._flush(audit)

    def list_for_run(self, project_id: UUID, run_id: UUID) -> list[CitationAudit]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(CitationAudit)
                .where(
                    CitationAudit.project_id == project_id,
                    CitationAudit.run_id == run_id,
                )
                .order_by(CitationAudit.audited_at.asc(), CitationAudit.id.asc())
            )
        )

    def list_for_section(
        self,
        project_id: UUID,
        draft_section_id: UUID,
    ) -> list[CitationAudit]:
        return list(
            self.session.scalars(
                select(CitationAudit)
                .where(
                    CitationAudit.project_id == project_id,
                    CitationAudit.draft_section_id == draft_section_id,
                )
                .order_by(CitationAudit.audited_at.asc(), CitationAudit.id.asc())
            )
        )
