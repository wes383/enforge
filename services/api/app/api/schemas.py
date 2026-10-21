from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from dr_core.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    CitationStyle,
    ParserStatus,
    ProjectLanguage,
    ProjectStatus,
    ResearchDepth,
    RunEventType,
    RunStatus,
)


class OrmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectResponse(OrmResponse):
    id: UUID
    title: str
    topic: str
    research_scope: str | None
    language: ProjectLanguage
    target_audience: str | None
    research_depth: ResearchDepth
    citation_style: CitationStyle
    budget_usd: Decimal | None
    status: ProjectStatus
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    limit: int
    offset: int


class RunResponse(OrmResponse):
    id: UUID
    project_id: UUID
    status: RunStatus
    error_summary: str | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    workflow_thread_id: str
    checkpoint_id: str | None
    created_at: datetime
    updated_at: datetime


class EventResponse(OrmResponse):
    id: int
    run_id: UUID
    event_type: RunEventType
    stage: RunStatus | None
    message: str | None
    payload: dict[str, Any]
    created_at: datetime


class EventListResponse(BaseModel):
    items: list[EventResponse]


class ApprovalResponse(OrmResponse):
    id: UUID
    project_id: UUID
    run_id: UUID
    kind: ApprovalKind
    status: ApprovalStatus
    version: int
    payload: dict[str, Any]
    feedback: str | None
    decision_payload: dict[str, Any] | None
    created_at: datetime
    decided_at: datetime | None


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "modified"]
    feedback: str | None = Field(default=None, max_length=20000)
    content: dict[str, Any] | None = None


class FileUploadResponse(BaseModel):
    file_id: UUID
    source_id: UUID
    project_id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    parser_status: ParserStatus
    parser_version: str | None
    parse_error: str | None
    created_at: datetime
