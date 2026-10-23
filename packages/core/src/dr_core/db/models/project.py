from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dr_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from dr_core.db.enums import (
    ArticleType,
    CitationStyle,
    ProjectLanguage,
    ProjectStatus,
    ResearchDepth,
    RunStatus,
    SearchProviderName,
    SearchQueryStatus,
    SectionCoverageStatus,
    SectionRole,
)
from dr_core.db.types import str_enum

if TYPE_CHECKING:
    from dr_core.db.models.evidence import Claim, EvidenceItem
    from dr_core.db.models.source import Source


class ResearchProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_projects"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    research_scope: Mapped[str | None] = mapped_column(Text)
    article_type: Mapped[ArticleType] = mapped_column(
        str_enum(ArticleType, "article_type", length=32),
        default=ArticleType.INVESTIGATIVE_RESEARCH,
        server_default=ArticleType.INVESTIGATIVE_RESEARCH.value,
        nullable=False,
    )
    language: Mapped[ProjectLanguage] = mapped_column(
        str_enum(ProjectLanguage, "project_language", length=8),
        default=ProjectLanguage.ZH,
        server_default=ProjectLanguage.ZH.value,
        nullable=False,
    )
    target_audience: Mapped[str | None] = mapped_column(Text)
    research_depth: Mapped[ResearchDepth] = mapped_column(
        str_enum(ResearchDepth, "research_depth", length=16),
        default=ResearchDepth.STANDARD,
        server_default=ResearchDepth.STANDARD.value,
        nullable=False,
    )
    citation_style: Mapped[CitationStyle] = mapped_column(
        str_enum(CitationStyle, "citation_style", length=16),
        default=CitationStyle.APA,
        server_default=CitationStyle.APA.value,
        nullable=False,
    )
    target_words: Mapped[int | None] = mapped_column(Integer)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    status: Mapped[ProjectStatus] = mapped_column(
        str_enum(ProjectStatus, "project_status", length=16),
        default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    runs: Mapped[list[ResearchRun]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    outline_sections: Mapped[list[OutlineSectionRecord]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    search_queries: Mapped[list[SearchQuery]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sources: Mapped[list[Source]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    claims: Mapped[list[Claim]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    evidence_items: Mapped[list[EvidenceItem]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "budget_usd IS NULL OR budget_usd > 0",
            name="budget_positive",
        ),
        CheckConstraint(
            "target_words IS NULL OR (target_words >= 100 AND target_words <= 50000)",
            name="target_words_range",
        ),
        CheckConstraint(
            "date_from IS NULL OR date_to IS NULL OR date_to >= date_from",
            name="project_date_range_ordered",
        ),
        Index("ix_research_projects_status_created_at", "status", "created_at"),
    )


class ResearchRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_runs"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "run_status", length=32),
        default=RunStatus.QUEUED,
        server_default=RunStatus.QUEUED.value,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        default=Decimal("0"),
        server_default=text("0"),
        nullable=False,
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    workflow_thread_id: Mapped[str] = mapped_column(
        String(128),
        default=lambda: uuid4().hex,
        unique=True,
        nullable=False,
    )
    checkpoint_id: Mapped[str | None] = mapped_column(String(255))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    project: Mapped[ResearchProject] = relationship(back_populates="runs")
    outline_sections: Mapped[list[OutlineSectionRecord]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    search_queries: Mapped[list[SearchQuery]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint("cost_usd >= 0", name="cost_non_negative"),
        Index("ix_research_runs_project_id_created_at", "project_id", "created_at"),
        Index("ix_research_runs_project_id_status", "project_id", "status"),
    )


class OutlineSectionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outline_sections"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    section_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[SectionRole] = mapped_column(
        str_enum(SectionRole, "section_role", length=24),
        default=SectionRole.BODY,
        server_default=SectionRole.BODY.value,
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )
    search_terms: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    target_words: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    coverage_status: Mapped[SectionCoverageStatus] = mapped_column(
        str_enum(SectionCoverageStatus, "section_coverage_status", length=16),
        default=SectionCoverageStatus.OPEN,
        server_default=SectionCoverageStatus.OPEN.value,
        nullable=False,
    )

    project: Mapped[ResearchProject] = relationship(back_populates="outline_sections")
    run: Mapped[ResearchRun] = relationship(back_populates="outline_sections")

    __table_args__ = (
        UniqueConstraint("run_id", "section_hash"),
        CheckConstraint("priority >= 0", name="priority_non_negative"),
        CheckConstraint(
            "target_words IS NULL OR (target_words >= 50 AND target_words <= 5000)",
            name="target_words_range",
        ),
        Index("ix_outline_sections_project_id_run_id", "project_id", "run_id"),
        Index("ix_outline_sections_run_id_coverage_status", "run_id", "coverage_status"),
    )


class SearchQuery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "search_queries"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    outline_section_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("outline_sections.id", ondelete="SET NULL"),
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[SearchProviderName] = mapped_column(
        str_enum(SearchProviderName, "search_provider", length=32),
        nullable=False,
    )
    round_index: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    status: Mapped[SearchQueryStatus] = mapped_column(
        str_enum(SearchQueryStatus, "search_query_status", length=16),
        default=SearchQueryStatus.PENDING,
        server_default=SearchQueryStatus.PENDING.value,
        nullable=False,
    )
    result_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)

    project: Mapped[ResearchProject] = relationship(back_populates="search_queries")
    run: Mapped[ResearchRun] = relationship(back_populates="search_queries")

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "round_index",
            "provider",
            "outline_section_id",
            "query_hash",
        ),
        CheckConstraint("round_index >= 1", name="round_positive"),
        CheckConstraint("result_count >= 0", name="result_count_non_negative"),
        Index("ix_search_queries_project_id_run_id", "project_id", "run_id"),
        Index("ix_search_queries_run_id_status", "run_id", "status"),
    )
