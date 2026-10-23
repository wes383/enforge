import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalResponse,
)
from app.services.dispatch import dispatch_research_resume
from dr_core.db import get_database
from dr_core.db.enums import ApprovalKind, ApprovalStatus
from dr_core.db.repositories import (
    ProjectRepository,
    RunApprovalRepository,
    RunRepository,
)
from dr_core.research.models import Outline
from dr_core.services.approvals import (
    ApprovalConflict,
    decide_approval,
    list_approvals,
    reopen_approval,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["approvals"])


@router.get(
    "/runs/{run_id}/approvals",
    response_model=ApprovalListResponse,
)
def get_run_approvals(
    run_id: UUID,
    status_filter: Annotated[
        ApprovalStatus | None,
        Query(alias="status"),
    ] = None,
) -> ApprovalListResponse:
    approvals = list_approvals(get_database(), run_id, status=status_filter)
    return ApprovalListResponse(
        items=[ApprovalResponse.model_validate(item) for item in approvals]
    )


@router.post(
    "/runs/{run_id}/approvals/{approval_id}",
    response_model=ApprovalResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def decide_run_approval(
    run_id: UUID,
    approval_id: UUID,
    data: ApprovalDecisionRequest,
) -> ApprovalResponse:
    database = get_database()
    with database.session() as session:
        run = RunRepository(session).get(run_id)
        project = ProjectRepository(session).get(run.project_id)
        approval = RunApprovalRepository(session).get(
            approval_id,
            project_id=project.id,
            run_id=run_id,
        )
        kind = approval.kind

    decision = ApprovalStatus(data.decision)
    content = data.content if decision is ApprovalStatus.MODIFIED else None
    if decision is ApprovalStatus.MODIFIED:
        _validate_modified_content(kind, content)

    try:
        approval = decide_approval(
            database,
            run_id,
            approval_id,
            decision=decision,
            feedback=data.feedback,
            content=content,
        )
    except ApprovalConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        dispatch_research_resume(run_id, run.project_id, approval_id)
    except Exception as exc:
        logger.exception("Failed to dispatch approval resume for run %s", run_id)
        reopen_approval(database, run_id, approval_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task queue is unavailable; approval was not applied",
        ) from exc

    return ApprovalResponse.model_validate(approval)


def _validate_modified_content(
    kind: ApprovalKind,
    content: dict | None,
) -> None:
    if content is None:
        raise HTTPException(
            status_code=422,
            detail="Modified approvals require updated content",
        )
    try:
        if kind is ApprovalKind.OUTLINE:
            Outline.model_validate(content)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid approval content: {exc}",
        ) from exc
