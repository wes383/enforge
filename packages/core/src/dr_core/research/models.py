from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from dr_core.db.enums import (
    ApprovalKind,
    ApprovalStatus,
    ArticleType,
    CitationAuditStatus,
    CitationIssueType,
    CitationStyle,
    ClaimType,
    ClaimVerificationStatus,
    EvidenceType,
    EvidenceVerificationStatus,
    ProjectLanguage,
    ResearchDepth,
    SearchProviderName,
    SectionCoverageStatus,
    SectionRole,
    SourceOrigin,
    SourceType,
)
from dr_core.schemas.base import SchemaModel


class ResearchBrief(SchemaModel):
    project_id: UUID
    run_id: UUID
    title: str = Field(min_length=1, max_length=300)
    topic: str = Field(min_length=1, max_length=20000)
    research_scope: str | None = Field(default=None, max_length=20000)
    article_type: ArticleType = ArticleType.INVESTIGATIVE_RESEARCH
    language: ProjectLanguage = ProjectLanguage.ZH
    target_audience: str | None = Field(default=None, max_length=1000)
    research_depth: ResearchDepth = ResearchDepth.STANDARD
    citation_style: CitationStyle = CitationStyle.APA
    target_words: int | None = Field(default=None, ge=100, le=50000)
    date_from: date | None = None
    date_to: date | None = None
    budget_usd: Decimal | None = Field(default=None, gt=0)
    max_search_rounds: int = Field(default=3, ge=1, le=10)
    max_sources: int = Field(default=20, ge=1, le=200)
    max_queries_per_round: int = Field(default=6, ge=1, le=30)
    max_fetches_per_round: int = Field(default=8, ge=0, le=100)
    max_evidence: int = Field(default=40, ge=1, le=500)
    token_budget: int = Field(default=0, ge=0)
    cost_budget_usd: Decimal = Field(default=Decimal("0"), ge=0)


class ResearchRecon(SchemaModel):
    """Grounding notes gathered by the bounded tool-using researcher.

    This is context for planning only. It is never cited and never treated as
    evidence; sources live in the database and go through the normal pipeline.
    """

    summary: str = Field(min_length=1, max_length=4000)
    themes: list[str] = Field(default_factory=list, max_length=6)
    terminology: list[str] = Field(default_factory=list, max_length=12)
    controversies: list[str] = Field(default_factory=list, max_length=6)
    source_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _clean_lists(self) -> "ResearchRecon":
        self.summary = " ".join(self.summary.split())[:4000]
        self.themes = _clean_items(self.themes, 6, 300)
        self.terminology = _clean_items(self.terminology, 12, 120)
        self.controversies = _clean_items(self.controversies, 6, 600)
        return self


def _clean_items(values: list[str], limit: int, chars: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value).split())[:chars]
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


class SearchQuery(SchemaModel):
    query_text: str = Field(min_length=1, max_length=2000)
    kind: Literal["web", "academic"]
    provider: SearchProviderName
    round_index: int = Field(default=1, ge=1, le=10)
    outline_section_id: UUID | None = None


class SourceRecord(SchemaModel):
    source_id: UUID
    source_version_id: UUID | None = None
    title: str = Field(min_length=1, max_length=5000)
    url: str | None = Field(default=None, max_length=4096)
    fetch_url: str | None = Field(default=None, max_length=4096)
    doi: str | None = Field(default=None, max_length=255)
    authors: list[str] = Field(default_factory=list, max_length=200)
    published_at: date | None = None
    source_type: SourceType = SourceType.WEB_PAGE
    source_origin: SourceOrigin = SourceOrigin.WEB_SEARCH
    provider: str = Field(min_length=1, max_length=64)
    score: float | None = Field(default=None, ge=0, le=1)
    rank_score: float | None = Field(default=None, ge=0)
    snippet: str | None = Field(default=None, max_length=100000)
    has_full_text: bool = False
    parser_status: str | None = None
    section_ids: list[UUID] = Field(default_factory=list)
    rank: int = Field(default=0, ge=0)


class EvidenceRecord(SchemaModel):
    evidence_id: UUID
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
    created_at: datetime | None = None
    independent_key: str | None = None


class QualityReason(SchemaModel):
    dimension: str = Field(min_length=1, max_length=64)
    status: Literal["positive", "neutral", "concern", "unknown"]
    reason: str = Field(min_length=1, max_length=2000)


class SourceQuality(SchemaModel):
    source_id: UUID
    identity_key: str = Field(min_length=1, max_length=512)
    level: Literal["high", "moderate", "low", "unknown"] = "unknown"
    is_primary: bool = False
    reasons: list[QualityReason] = Field(default_factory=list, max_length=20)


class EvidenceQuality(SchemaModel):
    evidence_id: UUID
    level: Literal["high", "moderate", "low", "uncertain"] = "uncertain"
    directness: Literal["direct", "indirect", "unclear"] = "unclear"
    context_consistency: Literal["preserved", "quote_only", "unclear"] = "unclear"
    independence: Literal["independent", "shared", "unknown"] = "unknown"
    counter_evidence: Literal["present", "none_found"] = "none_found"
    applicability: Literal["conditional", "not_explicit", "unknown"] = "unknown"
    reasons: list[QualityReason] = Field(default_factory=list, max_length=20)


class ResearchGap(SchemaModel):
    section_id: UUID | None = None
    section_title: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=20000)
    severity: Literal["low", "medium", "high"] = "medium"
    suggested_queries: list[str] = Field(default_factory=list, max_length=10)
    # Set on gaps raised for a section that is supported but still below its
    # evidence target, so a later round can tell whether new searching
    # actually helped.
    target_evidence: int = Field(default=0, ge=0)
    achieved_ratio: float = Field(default=0.0, ge=0, le=1)


class EvidenceRow(SchemaModel):
    section_id: UUID | None = None
    section_title: str = Field(min_length=1, max_length=1000)
    support_count: int = Field(default=0, ge=0)
    refute_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    independent_source_count: int = Field(default=0, ge=0)
    coverage_status: SectionCoverageStatus = SectionCoverageStatus.OPEN
    # How much evidence this section actually needs, and how much of that
    # target it reached. The coverage status answers "is this section
    # supportable at all"; the ratio answers "is it well enough supported".
    target_evidence: int = Field(default=0, ge=0)
    target_sources: int = Field(default=0, ge=0)
    evidence_ratio: float = Field(default=0.0, ge=0, le=1)
    conclusion: str = Field(default="", max_length=20000)
    support_evidence_ids: list[UUID] = Field(default_factory=list)
    refute_evidence_ids: list[UUID] = Field(default_factory=list)
    source_quality: list[SourceQuality] = Field(default_factory=list)
    evidence_quality: list[EvidenceQuality] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    gap: str | None = Field(default=None, max_length=20000)


class EvidenceMatrix(SchemaModel):
    rows: list[EvidenceRow] = Field(default_factory=list)
    total_evidence: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    independent_source_count: int = Field(default=0, ge=0)
    contradiction_count: int = Field(default=0, ge=0)
    coverage_ratio: float = Field(default=0.0, ge=0, le=1)
    # Share of the per-section evidence targets the run actually reached.
    target_ratio: float = Field(default=0.0, ge=0, le=1)
    duplicate_source_groups: list[str] = Field(default_factory=list)
    quality_notes: list[str] = Field(default_factory=list)


class OutlineSection(SchemaModel):
    section_id: UUID = Field(default_factory=uuid4)
    index: int = Field(ge=0)
    role: SectionRole = SectionRole.BODY
    title: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=5000)
    search_terms: list[str] = Field(default_factory=list, max_length=20)
    key_claims: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[UUID] = Field(default_factory=list)
    support_evidence_ids: list[UUID] = Field(default_factory=list)
    refute_evidence_ids: list[UUID] = Field(default_factory=list)
    target_words: int = Field(default=400, ge=50, le=5000)
    coverage_status: SectionCoverageStatus = SectionCoverageStatus.OPEN
    status: Literal["planned", "drafted", "verified"] = "planned"

    @field_validator("search_terms")
    @classmethod
    def _clean_search_terms(cls, terms: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in terms:
            text = " ".join(str(value).split()).strip()[:200]
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
            if len(cleaned) >= 5:
                break
        return cleaned

    @field_validator("search_terms", mode="before")
    @classmethod
    def _terms_from_scalar(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class Outline(SchemaModel):
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=1000)
    sections: list[OutlineSection] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=20000)

    @field_validator("sections")
    @classmethod
    def _ordered_unique_sections(
        cls,
        sections: list[OutlineSection],
    ) -> list[OutlineSection]:
        indexes = [section.index for section in sections]
        if indexes != list(range(len(sections))):
            raise ValueError("Outline section indexes must be contiguous from zero")
        return sections


class CitationIssue(SchemaModel):
    section_index: int = Field(ge=0)
    status: CitationAuditStatus
    issue_type: CitationIssueType | None = None
    details: str = Field(min_length=1, max_length=20000)


class CitationAudit(SchemaModel):
    issues: list[CitationIssue] = Field(default_factory=list)
    passed: int = Field(default=0, ge=0)
    warnings: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class ClaimVerification(SchemaModel):
    claim_id: UUID | None = None
    section_id: UUID | None = None
    section_index: int = Field(ge=0)
    claim_text: str = Field(min_length=1, max_length=50000)
    claim_type: ClaimType = ClaimType.FACTUAL
    status: ClaimVerificationStatus = ClaimVerificationStatus.UNSUPPORTED
    citation_markers: list[str] = Field(default_factory=list, max_length=50)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class ReferenceAudit(SchemaModel):
    cited_source_ids: list[UUID] = Field(default_factory=list)
    unused_source_ids: list[UUID] = Field(default_factory=list)
    invalid_metadata_source_ids: list[UUID] = Field(default_factory=list)
    dangling_markers: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ApprovalRequest(SchemaModel):
    kind: ApprovalKind
    version: int = Field(ge=1)
    payload: dict


class ApprovalDecision(SchemaModel):
    decision: ApprovalStatus
    feedback: str | None = Field(default=None, max_length=20000)
    content: dict | None = None

    @field_validator("decision")
    @classmethod
    def _decision_value(cls, decision: ApprovalStatus) -> ApprovalStatus:
        allowed = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.MODIFIED,
        }
        if decision not in allowed:
            raise ValueError("Approval decision must be approved, rejected, or modified")
        return decision
