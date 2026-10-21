from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from dr_core.db.enums import ApprovalKind, ApprovalStatus
from dr_core.db.models import RunApproval
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound, RepositoryError


class RunApprovalRepository(Repository):
    def request(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        kind: ApprovalKind,
        version: int,
        payload: dict,
    ) -> RunApproval:
        self._require_run(run_id, project_id)
        existing = self.find(project_id, run_id, kind, version)
        if existing is not None:
            return existing

        approval = RunApproval(
            project_id=project_id,
            run_id=run_id,
            kind=kind,
            version=version,
            payload=payload,
        )
        return self._insert_or_get(
            approval,
            lambda: self.find(project_id, run_id, kind, version),
        )

    def find(
        self,
        project_id: UUID,
        run_id: UUID,
        kind: ApprovalKind,
        version: int,
    ) -> RunApproval | None:
        return self.session.scalar(
            select(RunApproval).where(
                RunApproval.project_id == project_id,
                RunApproval.run_id == run_id,
                RunApproval.kind == kind,
                RunApproval.version == version,
            )
        )

    def get(
        self,
        approval_id: UUID,
        *,
        project_id: UUID,
        run_id: UUID | None = None,
    ) -> RunApproval:
        approval = self.session.get(RunApproval, approval_id)
        if (
            approval is None
            or approval.project_id != project_id
            or (run_id is not None and approval.run_id != run_id)
        ):
            raise EntityNotFound("Run approval not found")
        return approval

    def list_for_run(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[RunApproval]:
        self._require_run(run_id, project_id)
        statement = select(RunApproval).where(
            RunApproval.project_id == project_id,
            RunApproval.run_id == run_id,
        )
        if status is not None:
            statement = statement.where(RunApproval.status == status)
        return list(
            self.session.scalars(
                statement.order_by(
                    RunApproval.created_at.asc(),
                    RunApproval.id.asc(),
                )
            )
        )

    def latest(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        kind: ApprovalKind | None = None,
    ) -> RunApproval | None:
        self._require_run(run_id, project_id)
        statement = select(RunApproval).where(
            RunApproval.project_id == project_id,
            RunApproval.run_id == run_id,
        )
        if kind is not None:
            statement = statement.where(RunApproval.kind == kind)
        return self.session.scalar(
            statement.order_by(RunApproval.created_at.desc()).limit(1)
        )

    def decide(
        self,
        approval_id: UUID,
        *,
        project_id: UUID,
        decision: ApprovalStatus,
        feedback: str | None = None,
        decision_payload: dict | None = None,
    ) -> RunApproval:
        if decision not in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.MODIFIED,
        }:
            raise RepositoryError("Unsupported approval decision")
        approval = self.get(approval_id, project_id=project_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise RepositoryError("Approval has already been decided")
        approval.status = decision
        approval.feedback = feedback
        approval.decision_payload = decision_payload
        approval.decided_at = datetime.now(tz=UTC)
        self.session.flush()
        return approval

    def cancel_pending(self, run_id: UUID) -> int:
        result = self.session.execute(
            update(RunApproval)
            .where(
                RunApproval.run_id == run_id,
                RunApproval.status == ApprovalStatus.PENDING,
            )
            .values(
                status=ApprovalStatus.CANCELLED,
                decided_at=datetime.now(tz=UTC),
            )
        )
        self.session.flush()
        return int(result.rowcount or 0)
