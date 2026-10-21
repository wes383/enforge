from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from dr_core.db.enums import (
    AuditMethod,
    CitationAuditStatus,
    CitationIssueType,
    DraftSectionStatus,
)
from dr_core.schemas.base import SchemaModel


class OutlineCreate(SchemaModel):
    content: dict[str, Any]
    version_note: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _require_content(self) -> "OutlineCreate":
        if not self.content:
            raise ValueError("Outline content must not be empty")
        return self


class DraftSectionCreate(SchemaModel):
    outline_id: UUID | None = None
    section_index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=1000)
    content_markdown: str = ""
    citation_map: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[UUID] = Field(default_factory=list)
    generation_status: DraftSectionStatus = DraftSectionStatus.DRAFTED


class CitationAuditCreate(SchemaModel):
    draft_section_id: UUID
    claim_id: UUID | None = None
    citation_marker: str | None = Field(default=None, max_length=64)
    status: CitationAuditStatus
    issue_type: CitationIssueType | None = None
    details: str = Field(min_length=1, max_length=20000)
    audit_method: AuditMethod = AuditMethod.DETERMINISTIC
    correction_note: str | None = Field(default=None, max_length=20000)
    corrected_in_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_issue(self) -> "CitationAuditCreate":
        if self.status != CitationAuditStatus.PASSED and self.issue_type is None:
            raise ValueError("issue_type is required for non-passing audits")
        return self
