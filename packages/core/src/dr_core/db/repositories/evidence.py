from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from dr_core.db.enums import (
    ClaimVerificationStatus,
    EvidenceVerificationStatus,
)
from dr_core.db.models import (
    Claim,
    ClaimEvidence,
    EvidenceItem,
    ResearchQuestion,
    SourceVersion,
)
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound, RepositoryError
from dr_core.normalization import claim_fingerprint, evidence_fingerprint
from dr_core.schemas import ClaimCreate, ClaimEvidenceCreate, EvidenceCreate


class EvidenceRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: EvidenceCreate,
    ) -> EvidenceItem:
        self._require_run(run_id, project_id)
        version = self.session.get(SourceVersion, data.source_version_id)
        if (
            version is None
            or version.project_id != project_id
            or version.source_id != data.source_id
        ):
            raise EntityNotFound("Source version not found for the given source")

        if data.research_question_id is not None:
            question = self.session.get(
                ResearchQuestion,
                data.research_question_id,
            )
            if (
                question is None
                or question.project_id != project_id
                or question.run_id != run_id
            ):
                raise EntityNotFound("Research question not found in the run")

        fingerprint = evidence_fingerprint(
            str(data.source_version_id),
            data.quoted_text,
            str(data.research_question_id)
            if data.research_question_id is not None
            else None,
        )
        existing = self.find_by_fingerprint(run_id, fingerprint)
        if existing is not None:
            return existing

        evidence = EvidenceItem(
            project_id=project_id,
            run_id=run_id,
            research_question_id=data.research_question_id,
            source_id=data.source_id,
            source_version_id=data.source_version_id,
            evidence_type=data.evidence_type,
            claim_summary=data.claim_summary,
            quoted_text=data.quoted_text,
            context_before=data.context_before,
            context_after=data.context_after,
            locator=data.locator,
            verification_status=data.verification_status,
            evidence_fingerprint=fingerprint,
        )
        return self._insert_or_get(
            evidence,
            lambda: self.find_by_fingerprint(run_id, fingerprint),
        )

    def find_by_fingerprint(
        self,
        run_id: UUID,
        fingerprint: str,
    ) -> EvidenceItem | None:
        return self.session.scalar(
            select(EvidenceItem).where(
                EvidenceItem.run_id == run_id,
                EvidenceItem.evidence_fingerprint == fingerprint,
            )
        )

    def get(
        self,
        evidence_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> EvidenceItem:
        evidence = self.session.get(EvidenceItem, evidence_id)
        if evidence is None or (
            project_id is not None and evidence.project_id != project_id
        ):
            raise EntityNotFound("Evidence item not found")
        return evidence

    def list_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
    ) -> list[EvidenceItem]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.project_id == project_id,
                    EvidenceItem.run_id == run_id,
                )
                .order_by(EvidenceItem.created_at.asc(), EvidenceItem.id.asc())
            )
        )

    def list_for_question(
        self,
        project_id: UUID,
        question_id: UUID,
    ) -> list[EvidenceItem]:
        return list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.project_id == project_id,
                    EvidenceItem.research_question_id == question_id,
                )
                .order_by(EvidenceItem.created_at.asc(), EvidenceItem.id.asc())
            )
        )

    def list_for_source_version(
        self,
        project_id: UUID,
        source_version_id: UUID,
    ) -> list[EvidenceItem]:
        return list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.project_id == project_id,
                    EvidenceItem.source_version_id == source_version_id,
                )
                .order_by(EvidenceItem.created_at.asc(), EvidenceItem.id.asc())
            )
        )

    def mark_verification(
        self,
        evidence_id: UUID,
        status: EvidenceVerificationStatus,
        *,
        project_id: UUID,
    ) -> EvidenceItem:
        evidence = self.get(evidence_id, project_id=project_id)
        evidence.verification_status = status
        self.session.flush()
        return evidence


class ClaimRepository(Repository):
    def create(
        self,
        project_id: UUID,
        run_id: UUID,
        data: ClaimCreate,
    ) -> Claim:
        self._require_run(run_id, project_id)
        fingerprint = claim_fingerprint(data.claim_text)
        existing = self.find_by_fingerprint(run_id, fingerprint)
        if existing is not None:
            return existing

        claim = Claim(
            project_id=project_id,
            run_id=run_id,
            claim_text=data.claim_text,
            claim_fingerprint=fingerprint,
            claim_type=data.claim_type,
            verification_status=data.verification_status,
        )
        return self._insert_or_get(
            claim,
            lambda: self.find_by_fingerprint(run_id, fingerprint),
        )

    def find_by_fingerprint(
        self,
        run_id: UUID,
        fingerprint: str,
    ) -> Claim | None:
        return self.session.scalar(
            select(Claim).where(
                Claim.run_id == run_id,
                Claim.claim_fingerprint == fingerprint,
            )
        )

    def get(self, claim_id: UUID, *, project_id: UUID | None = None) -> Claim:
        claim = self.session.get(Claim, claim_id)
        if claim is None or (project_id is not None and claim.project_id != project_id):
            raise EntityNotFound("Claim not found")
        return claim

    def list_for_run(self, project_id: UUID, run_id: UUID) -> list[Claim]:
        self._require_run(run_id, project_id)
        return list(
            self.session.scalars(
                select(Claim)
                .where(
                    Claim.project_id == project_id,
                    Claim.run_id == run_id,
                )
                .order_by(Claim.created_at.asc(), Claim.id.asc())
            )
        )

    def update_verification(
        self,
        claim_id: UUID,
        status: ClaimVerificationStatus,
        *,
        project_id: UUID,
    ) -> Claim:
        claim = self.get(claim_id, project_id=project_id)
        claim.verification_status = status
        self.session.flush()
        return claim


class ClaimEvidenceRepository(Repository):
    def link(
        self,
        project_id: UUID,
        claim_id: UUID,
        data: ClaimEvidenceCreate,
    ) -> ClaimEvidence:
        claim = self.session.get(Claim, claim_id)
        if claim is None or claim.project_id != project_id:
            raise EntityNotFound("Claim not found")
        evidence = self.session.get(EvidenceItem, data.evidence_id)
        if evidence is None or evidence.project_id != project_id:
            raise EntityNotFound("Evidence item not found")
        if evidence.run_id != claim.run_id:
            raise RepositoryError("Claim and evidence must belong to the same run")

        existing = self.find(claim_id, data.evidence_id, project_id=project_id)
        if existing is not None:
            existing.relation = data.relation
            if data.rationale is not None:
                existing.rationale = data.rationale
            self.session.flush()
            return existing

        link = ClaimEvidence(
            project_id=project_id,
            claim_id=claim_id,
            evidence_id=data.evidence_id,
            relation=data.relation,
            rationale=data.rationale,
        )
        return self._insert_or_get(
            link,
            lambda: self.find(claim_id, data.evidence_id, project_id=project_id),
        )

    def find(
        self,
        claim_id: UUID,
        evidence_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> ClaimEvidence | None:
        statement = select(ClaimEvidence).where(
            ClaimEvidence.claim_id == claim_id,
            ClaimEvidence.evidence_id == evidence_id,
        )
        if project_id is not None:
            statement = statement.where(ClaimEvidence.project_id == project_id)
        return self.session.scalar(statement)

    def list_for_claim(
        self,
        project_id: UUID,
        claim_id: UUID,
    ) -> list[ClaimEvidence]:
        claim = self.session.get(Claim, claim_id)
        if claim is None or claim.project_id != project_id:
            raise EntityNotFound("Claim not found")
        return list(
            self.session.scalars(
                select(ClaimEvidence)
                .where(
                    ClaimEvidence.project_id == project_id,
                    ClaimEvidence.claim_id == claim_id,
                )
                .order_by(
                    ClaimEvidence.relation.asc(),
                    ClaimEvidence.created_at.asc(),
                )
            )
        )

    def list_for_evidence(
        self,
        project_id: UUID,
        evidence_id: UUID,
    ) -> list[ClaimEvidence]:
        evidence = self.session.get(EvidenceItem, evidence_id)
        if evidence is None or evidence.project_id != project_id:
            raise EntityNotFound("Evidence item not found")
        return list(
            self.session.scalars(
                select(ClaimEvidence)
                .where(
                    ClaimEvidence.project_id == project_id,
                    ClaimEvidence.evidence_id == evidence_id,
                )
                .order_by(ClaimEvidence.created_at.asc())
            )
        )

    def unlink(self, project_id: UUID, claim_id: UUID, evidence_id: UUID) -> bool:
        link = self.find(claim_id, evidence_id, project_id=project_id)
        if link is None:
            return False
        self.session.delete(link)
        self.session.flush()
        return True
