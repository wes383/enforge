from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dr_core.db.base import Base
from dr_core.db.enums import RunEventType, RunStatus
from dr_core.db.types import str_enum


class RunEvent(Base):
    __tablename__ = "run_events"

    # A monotonic identity column doubles as the SSE Last-Event-ID cursor.
    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[RunEventType] = mapped_column(
        str_enum(RunEventType, "run_event_type", length=32),
        nullable=False,
    )
    stage: Mapped[RunStatus | None] = mapped_column(
        str_enum(RunStatus, "run_status", length=32),
    )
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("run_id", "dedupe_key"),
        Index("ix_run_events_run_id_id", "run_id", "id"),
        Index("ix_run_events_project_id_created_at", "project_id", "created_at"),
    )
