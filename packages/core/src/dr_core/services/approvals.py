from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from dr_core.db import Database
from dr_core.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    OutlineStatus,
    RunEventType,
    RunStatus,
)
from dr_core.db.models import ResearchRun, RunApproval
from dr_core.db.repositories import (
    OutlineRepository,
    ProjectRepository,
    RunApprovalRepository,
    RunEventRepository,
)
from dr_core.db.repositories.exceptions import EntityNotFound, RepositoryError
from dr_core.schemas import RunEventCreate
from dr_core.services.runs import lock_run


class ApprovalConflict(RepositoryError):
    pass


def decide_approval(
    database: Database,
    run_id: UUID,
    approval_id: UUID,
    *,
    decision: ApprovalStatus,
    feedback: str | None = None,
    content: dict | None = None,
) -> RunApproval:
    with database.session_scope() as session:
        approval = session.scalar(
            select(RunApproval)
            .where(RunApproval.id == approval_id)
            .with_for_update()
        )
        if approval is None or approval.run_id != run_id:
            raise EntityNotFound("Run approval not found")
        run = lock_run(session, run_id, approval.project_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        if run.status is not RunStatus.AWAITING_APPROVAL:
            raise ApprovalConflict("Run is not awaiting approval")
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalConflict("Approval has already been decided")

        approval = RunApprovalRepository(session).decide(
            approval_id,
            project_id=approval.project_id,
            decision=decision,
            feedback=feedback,
            decision_payload=content,
        )
        _apply_outline_decision(
            session,
            approval,
            decision=decision,
            content=content,
        )
        RunEventRepository(session).append(
            approval.project_id,
            run_id,
            RunEventCreate(
                event_type=RunEventType.APPROVAL_DECIDED,
                stage=RunStatus.AWAITING_APPROVAL,
                message=f"{approval.kind.value} approval recorded: {decision.value}",
                payload={
                    "approval_id": str(approval.id),
                    "kind": approval.kind.value,
                    "version": approval.version,
                    "decision": decision.value,
                },
                dedupe_key=f"approval:{approval.id}:decided",
            ),
        )
        session.flush()
        session.refresh(approval)
        return approval


def list_approvals(
    database: Database,
    run_id: UUID,
    *,
    status: ApprovalStatus | None = None,
) -> list[RunApproval]:
    with database.session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        ProjectRepository(session).get(run.project_id)
        approvals = RunApprovalRepository(session).list_for_run(
            run.project_id,
            run.id,
            status=status,
        )
        session.expunge_all()
        return approvals


def resume_payload(database: Database, run_id: UUID, approval_id: UUID) -> dict:
    with database.session() as session:
        run = session.get(ResearchRun, run_id)
        if run is None:
            raise EntityNotFound("Research run not found")
        approval = RunApprovalRepository(session).get(
            approval_id,
            project_id=run.project_id,
            run_id=run_id,
        )
        if approval.status not in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.MODIFIED,
        }:
            raise ApprovalConflict("Approval is still pending")
        return {
            "approval_id": str(approval.id),
            "kind": approval.kind.value,
            "version": approval.version,
            "decision": approval.status.value,
            "feedback": approval.feedback,
            "content": approval.decision_payload,
        }


def reopen_approval(database: Database, run_id: UUID, approval_id: UUID) -> bool:
    with database.session_scope() as session:
        approval = session.scalar(
            select(RunApproval)
            .where(RunApproval.id == approval_id)
            .with_for_update()
        )
        if approval is None or approval.run_id != run_id:
            return False
        if approval.status is ApprovalStatus.PENDING:
            return True
        if approval.status is ApprovalStatus.CANCELLED:
            return False
        approval.status = ApprovalStatus.PENDING
        approval.decided_at = None
        approval.feedback = None
        approval.decision_payload = None
        session.flush()
        return True


def _apply_outline_decision(
    session,
    approval: RunApproval,
    *,
    decision: ApprovalStatus,
    content: dict | None,
) -> None:
    if approval.kind is not ApprovalKind.OUTLINE:
        return
    outline_id = approval.payload.get("outline_id")
    if not outline_id:
        return
    repo = OutlineRepository(session)
    outline = repo.get(UUID(str(outline_id)), project_id=approval.project_id)
    if decision is ApprovalStatus.APPROVED:
        repo.approve(outline.id, project_id=approval.project_id)
    elif decision is ApprovalStatus.REJECTED:
        repo.reject(outline.id, project_id=approval.project_id)
    elif decision is ApprovalStatus.MODIFIED:
        if content:
            outline.content = content
        outline.status = OutlineStatus.APPROVED
        outline.approved_at = datetime.now(tz=UTC)
        session.flush()
