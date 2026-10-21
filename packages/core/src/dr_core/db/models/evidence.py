from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dr_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from dr_core.db.enums import (
    ClaimType,
    ClaimVerificationStatus,
    EvidenceRelation,
    EvidenceType,
    EvidenceVerificationStatus,
)
from dr_core.db.types import str_enum

if TYPE_CHECKING:
    from dr_core.db.models.project import ResearchProject, ResearchQuestion, ResearchRun
    from dr_core.db.models.source import Source, SourceVersion


class EvidenceItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evidence_items"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    research_question_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_questions.id", ondelete="SET NULL"),
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        str_enum(EvidenceType, "evidence_type", length=32),
        default=EvidenceType.DIRECT_SUPPORT,
        server_default=EvidenceType.DIRECT_SUPPORT.value,
        nullable=False,
    )
    claim_summary: Mapped[str] = mapped_column(Text, nullable=False)
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_before: Mapped[str | None] = mapped_column(Text)
    context_after: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    verification_status: Mapped[EvidenceVerificationStatus] = mapped_column(
        str_enum(EvidenceVerificationStatus, "evidence_verification_status", length=16),
        default=EvidenceVerificationStatus.PENDING,
        server_default=EvidenceVerificationStatus.PENDING.value,
        nullable=False,
    )
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project: Mapped[ResearchProject] = relationship(back_populates="evidence_items")
    run: Mapped[ResearchRun] = relationship()
    research_question: Mapped[ResearchQuestion | None] = relationship()
    source: Mapped[Source] = relationship()
    source_version: Mapped[SourceVersion] = relationship(back_populates="evidence_items")
    claim_links: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="evidence",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("run_id", "evidence_fingerprint"),
        Index(
            "ix_evidence_items_project_id_run_id",
            "project_id",
            "run_id",
        ),
        Index(
            "ix_evidence_items_research_question_id",
            "research_question_id",
        ),
        Index("ix_evidence_items_source_version_id", "source_version_id"),
    )


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claims"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_type: Mapped[ClaimType] = mapped_column(
        str_enum(ClaimType, "claim_type", length=24),
        default=ClaimType.FACTUAL,
        server_default=ClaimType.FACTUAL.value,
        nullable=False,
    )
    verification_status: Mapped[ClaimVerificationStatus] = mapped_column(
        str_enum(ClaimVerificationStatus, "claim_verification_status", length=24),
        default=ClaimVerificationStatus.PENDING,
        server_default=ClaimVerificationStatus.PENDING.value,
        nullable=False,
    )

    project: Mapped[ResearchProject] = relationship(back_populates="claims")
    run: Mapped[ResearchRun] = relationship()
    evidence_links: Mapped[list[ClaimEvidence]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("run_id", "claim_fingerprint"),
        Index("ix_claims_project_id_run_id", "project_id", "run_id"),
        Index("ix_claims_run_id_verification_status", "run_id", "verification_status"),
    )


class ClaimEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "claim_evidence"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[EvidenceRelation] = mapped_column(
        str_enum(EvidenceRelation, "evidence_relation", length=32),
        nullable=False,
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    claim: Mapped[Claim] = relationship(back_populates="evidence_links")
    evidence: Mapped[EvidenceItem] = relationship(back_populates="claim_links")

    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id"),
        Index("ix_claim_evidence_project_id", "project_id"),
        Index("ix_claim_evidence_evidence_id", "evidence_id"),
    )
