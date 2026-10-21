from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    AuditMethod,
    CitationAuditStatus,
    CitationIssueType,
    DraftSectionStatus,
    OutlineStatus,
)
from dr_core.db.types import str_enum


class Outline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outlines"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OutlineStatus] = mapped_column(
        str_enum(OutlineStatus, "outline_status", length=16),
        default=OutlineStatus.DRAFT,
        server_default=OutlineStatus.DRAFT.value,
        nullable=False,
    )
    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    version_note: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_outlines_project_id_run_id", "project_id", "run_id"),
        Index("ix_outlines_run_id_status", "run_id", "status"),
    )


class DraftSection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "draft_sections"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    outline_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("outlines.id", ondelete="SET NULL"),
    )
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_markdown: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )
    citation_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_status: Mapped[DraftSectionStatus] = mapped_column(
        str_enum(DraftSectionStatus, "draft_section_status", length=16),
        default=DraftSectionStatus.PENDING,
        server_default=DraftSectionStatus.PENDING.value,
        nullable=False,
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))

    outline: Mapped[Outline | None] = relationship()
    citation_audits: Mapped[list[CitationAudit]] = relationship(
        back_populates="draft_section",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("run_id", "section_index", "version"),
        CheckConstraint("section_index >= 0", name="section_index_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_draft_sections_project_id_run_id", "project_id", "run_id"),
        Index("ix_draft_sections_run_id_section_index", "run_id", "section_index"),
    )


class CitationAudit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "citation_audits"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("draft_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL"),
    )
    citation_marker: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[CitationAuditStatus] = mapped_column(
        str_enum(CitationAuditStatus, "citation_audit_status", length=16),
        nullable=False,
    )
    issue_type: Mapped[CitationIssueType | None] = mapped_column(
        str_enum(CitationIssueType, "citation_issue_type", length=32),
    )
    details: Mapped[str] = mapped_column(Text, nullable=False)
    audit_method: Mapped[AuditMethod] = mapped_column(
        str_enum(AuditMethod, "audit_method", length=16),
        default=AuditMethod.DETERMINISTIC,
        server_default=AuditMethod.DETERMINISTIC.value,
        nullable=False,
    )
    audited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    correction_note: Mapped[str | None] = mapped_column(Text)
    corrected_in_version: Mapped[int | None] = mapped_column(Integer)

    draft_section: Mapped[DraftSection] = relationship(back_populates="citation_audits")

    __table_args__ = (
        CheckConstraint(
            "corrected_in_version IS NULL OR corrected_in_version >= 1",
            name="corrected_version_positive",
        ),
        Index("ix_citation_audits_project_id_run_id", "project_id", "run_id"),
        Index("ix_citation_audits_draft_section_id", "draft_section_id"),
    )
