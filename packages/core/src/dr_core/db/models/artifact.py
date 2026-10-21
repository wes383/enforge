from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from dr_core.db.base import Base, UUIDPrimaryKeyMixin
from dr_core.db.enums import ArtifactStatus, ArtifactType
from dr_core.db.types import RELATIVE_STORAGE_KEY_SQL, str_enum


class Artifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "artifacts"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[ArtifactType] = mapped_column(
        str_enum(ArtifactType, "artifact_type", length=32),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ArtifactStatus] = mapped_column(
        str_enum(ArtifactStatus, "artifact_status", length=16),
        default=ArtifactStatus.READY,
        server_default=ArtifactStatus.READY.value,
        nullable=False,
    )
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
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

    __table_args__ = (
        UniqueConstraint("run_id", "artifact_type", "version"),
        UniqueConstraint("storage_key"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint(RELATIVE_STORAGE_KEY_SQL, name="relative_storage_key"),
        Index("ix_artifacts_project_id_run_id", "project_id", "run_id"),
    )
