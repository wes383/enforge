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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dr_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from dr_core.db.enums import ApprovalKind, ApprovalStatus
from dr_core.db.types import str_enum


class RunApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_approvals"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ApprovalKind] = mapped_column(
        str_enum(ApprovalKind, "approval_kind", length=16),
        nullable=False,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus, "approval_status", length=16),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("run_id", "kind", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_run_approvals_project_id_run_id", "project_id", "run_id"),
        Index("ix_run_approvals_run_id_status", "run_id", "status"),
    )
