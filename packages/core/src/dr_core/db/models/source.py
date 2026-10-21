from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dr_core.config import get_settings
from dr_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from dr_core.db.enums import (
    ParserStatus,
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.db.types import RELATIVE_STORAGE_KEY_SQL, str_enum

if TYPE_CHECKING:
    from dr_core.db.models.evidence import EvidenceItem
    from dr_core.db.models.project import ResearchProject


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    published_at: Mapped[date | None] = mapped_column(Date)
    doi: Mapped[str | None] = mapped_column(String(255))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    canonical_url_hash: Mapped[str | None] = mapped_column(String(64))
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, "source_type", length=32),
        default=SourceType.WEB_PAGE,
        server_default=SourceType.WEB_PAGE.value,
        nullable=False,
    )
    source_origin: Mapped[SourceOrigin] = mapped_column(
        str_enum(SourceOrigin, "source_origin", length=32),
        default=SourceOrigin.MANUAL,
        server_default=SourceOrigin.MANUAL.value,
        nullable=False,
    )
    language: Mapped[str | None] = mapped_column(String(16))
    venue: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )

    project: Mapped[ResearchProject] = relationship(back_populates="sources")
    versions: Mapped[list[SourceVersion]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "doi"),
        UniqueConstraint("project_id", "canonical_url_hash"),
        Index("ix_sources_project_id_source_type", "project_id", "source_type"),
        Index("ix_sources_project_id_published_at", "project_id", "published_at"),
    )


class SourceVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_versions"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_kind: Mapped[SourceVersionKind] = mapped_column(
        str_enum(SourceVersionKind, "source_version_kind", length=32),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    fetched_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    license: Mapped[str | None] = mapped_column(Text)
    parser_status: Mapped[ParserStatus] = mapped_column(
        str_enum(ParserStatus, "parser_status", length=16),
        default=ParserStatus.PENDING,
        server_default=ParserStatus.PENDING.value,
        nullable=False,
    )
    parser_version: Mapped[str | None] = mapped_column(String(64))
    parse_error: Mapped[str | None] = mapped_column(Text)
    version_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    source: Mapped[Source] = relationship(back_populates="versions")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="source_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    evidence_items: Mapped[list[EvidenceItem]] = relationship(
        back_populates="source_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("source_id", "version_number"),
        UniqueConstraint("source_id", "content_hash", "content_kind"),
        CheckConstraint("version_number >= 1", name="version_positive"),
        CheckConstraint(
            "content_kind = 'search_snippet' OR storage_key IS NOT NULL",
            name="requires_storage_key",
        ),
        CheckConstraint(
            f"storage_key IS NULL OR {RELATIVE_STORAGE_KEY_SQL}",
            name="relative_storage_key",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="size_non_negative",
        ),
        Index("ix_source_versions_project_id_source_id", "project_id", "source_id"),
        Index("ix_source_versions_source_id_parser_status", "source_id", "parser_status"),
    )


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_chunks"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    paragraph_start: Mapped[int | None] = mapped_column(Integer)
    paragraph_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embedding_dimension),
    )
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    source_version: Mapped[SourceVersion] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("source_version_id", "chunk_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint(
            "page_start IS NULL OR page_start >= 1",
            name="page_start_positive",
        ),
        CheckConstraint(
            "page_end IS NULL OR page_start IS NULL OR page_end >= page_start",
            name="page_range_ordered",
        ),
        CheckConstraint(
            "paragraph_end IS NULL OR paragraph_start IS NULL "
            "OR paragraph_end >= paragraph_start",
            name="paragraph_range_ordered",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="token_count_non_negative",
        ),
        Index("ix_document_chunks_project_id_source_version_id", "project_id", "source_version_id"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_document_chunks_text_fts",
            sa_text("to_tsvector('simple', text)"),
            postgresql_using="gin",
        ),
    )
