from typing import Any
from uuid import UUID

from pydantic import Field

from dr_core.db.enums import (
    ClaimType,
    ClaimVerificationStatus,
    EvidenceRelation,
    EvidenceType,
    EvidenceVerificationStatus,
)
from dr_core.schemas.base import SchemaModel


class EvidenceCreate(SchemaModel):
    source_id: UUID
    source_version_id: UUID
    outline_section_id: UUID | None = None
    evidence_type: EvidenceType = EvidenceType.DIRECT_SUPPORT
    claim_summary: str = Field(min_length=1, max_length=20000)
    quoted_text: str = Field(min_length=1, max_length=100000)
    context_before: str | None = Field(default=None, max_length=50000)
    context_after: str | None = Field(default=None, max_length=50000)
    locator: dict[str, Any] = Field(default_factory=dict)
    verification_status: EvidenceVerificationStatus = (
        EvidenceVerificationStatus.PENDING
    )


class ClaimCreate(SchemaModel):
    claim_text: str = Field(min_length=1, max_length=50000)
    claim_type: ClaimType = ClaimType.FACTUAL
    verification_status: ClaimVerificationStatus = ClaimVerificationStatus.PENDING


class ClaimEvidenceCreate(SchemaModel):
    evidence_id: UUID
    relation: EvidenceRelation
    rationale: str | None = Field(default=None, max_length=20000)
