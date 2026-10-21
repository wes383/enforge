"""run cancellation

Revision ID: 8d2a7c4e9f10
Revises: 4f6d1c8a2b90
Create Date: 2026-10-21 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8d2a7c4e9f10"
down_revision: Union[str, None] = "4f6d1c8a2b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_EVENT_TYPES = (
    "phase_started",
    "phase_completed",
    "phase_failed",
    "progress",
    "source_fetched",
    "source_parsed",
    "evidence_extracted",
    "section_generated",
    "citation_audit_failed",
    "awaiting_approval",
    "approval_decided",
    "run_cancelled",
    "error",
    "other",
)

_NEW_EVENT_TYPES = (
    "run_created",
    "cancel_requested",
    "retry_scheduled",
    *_OLD_EVENT_TYPES,
)


def _event_type_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"event_type IN ({joined})"


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        op.f("ck_run_events_run_event_type"),
        "run_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_run_events_run_event_type"),
        "run_events",
        _event_type_check(_NEW_EVENT_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_run_events_run_event_type"),
        "run_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_run_events_run_event_type"),
        "run_events",
        _event_type_check(_OLD_EVENT_TYPES),
    )
    op.drop_column("research_runs", "cancel_requested_at")
