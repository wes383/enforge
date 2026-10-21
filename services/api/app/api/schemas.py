from datetime import date, datetime
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
    target_words: int | None
    date_from: date | None
    date_to: date | None
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


class RunListResponse(BaseModel):
    items: list[RunResponse]
    total: int
    limit: int
    offset: int


class ResearchQuestionResponse(OrmResponse):
    id: UUID
    question: str
    priority: int
    parent_id: UUID | None
    coverage_status: str
    created_at: datetime


class QuestionListResponse(BaseModel):
    items: list[ResearchQuestionResponse]


class SourceVersionResponse(BaseModel):
    id: UUID
    version_number: int
    content_kind: str
    mime_type: str | None
    size_bytes: int | None
    parser_status: str
    parser_version: str | None
    parse_error: str | None
    fetched_url: str | None
    fetched_at: datetime
    has_stored_file: bool
    metadata: dict[str, Any]


class SourceResponse(BaseModel):
    id: UUID
    title: str
    authors: list[str]
    published_at: date | None
    doi: str | None
    url: str | None
    source_type: str
    source_origin: str
    venue: str | None
    publisher: str | None
    metadata: dict[str, Any]
    versions: list[SourceVersionResponse]
    question_ids: list[UUID]
    evidence_count: int
    fetch_status: str | None


class SourceListResponse(BaseModel):
    items: list[SourceResponse]


class EvidenceResponse(BaseModel):
    id: UUID
    source_id: UUID
    source_version_id: UUID
    research_question_id: UUID | None
    evidence_type: str
    claim_summary: str
    quoted_text: str
    context_before: str | None
    context_after: str | None
    locator: dict[str, Any]
    verification_status: str
    source_title: str
    source_url: str | None
    source_type: str | None
    source_authors: list[str]
    source_published_at: date | None
    source_version_number: int | None
    source_version_kind: str | None
    created_at: datetime


class EvidenceListResponse(BaseModel):
    items: list[EvidenceResponse]
    total: int
    limit: int
    offset: int


class OutlineWorkspaceResponse(BaseModel):
    outline: dict[str, Any] | None
    version: int | None
    status: str | None
    approved_at: datetime | None


class SectionResponse(BaseModel):
    id: UUID
    run_id: UUID
    outline_id: UUID | None
    section_index: int
    title: str
    content_markdown: str
    citation_map: dict[str, str]
    evidence_ids: list[UUID]
    version: int
    generation_status: str
    content_hash: str | None
    created_at: datetime
    audit_status: str | None
    audit_details: list[str]


class SectionListResponse(BaseModel):
    items: list[SectionResponse]


class SectionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    content_markdown: str = Field(min_length=1, max_length=200000)


class ClaimEvidenceLinkResponse(BaseModel):
    evidence_id: UUID
    relation: str
    rationale: str | None


class ClaimResponse(BaseModel):
    id: UUID
    claim_text: str
    claim_type: str
    verification_status: str
    evidence: list[ClaimEvidenceLinkResponse]


class ClaimListResponse(BaseModel):
    items: list[ClaimResponse]


class ArtifactResponse(BaseModel):
    id: UUID
    artifact_type: str
    title: str | None
    version: int
    mime_type: str
    size_bytes: int
    content_hash: str
    status: str
    metadata: dict[str, Any]
    created_at: datetime


class ArtifactListResponse(BaseModel):
    items: list[ArtifactResponse]


class ExportResponse(BaseModel):
    artifacts: list[ArtifactResponse]
    claims: list[dict[str, Any]]
    citation_audit: dict[str, Any]
    reference_audit: dict[str, Any]
    section_count: int


class RunOverviewResponse(BaseModel):
    run: RunResponse
    project: ProjectResponse
    questions: list[ResearchQuestionResponse]
    counts: dict[str, int]
    source_status: dict[str, int]
    search_status: dict[str, int]
    gaps: list[dict[str, Any]]
    pending_approval: ApprovalResponse | None
    latest_events: list[EventResponse]
