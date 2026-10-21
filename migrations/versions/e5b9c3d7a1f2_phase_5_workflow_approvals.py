"""phase 5 workflow approvals

Revision ID: e5b9c3d7a1f2
Revises: c4e8f1a2b7d3
Create Date: 2026-10-21 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e5b9c3d7a1f2"
down_revision: Union[str, None] = "c4e8f1a2b7d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_approvals",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "plan",
                "outline",
                "evidence",
                name="approval_kind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "modified",
                "cancelled",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column(
            "decision_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_run_approvals_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["research_projects.id"],
            name=op.f("fk_run_approvals_project_id_research_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_run_approvals_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_approvals")),
        sa.UniqueConstraint(
            "run_id",
            "kind",
            "version",
            name=op.f("uq_run_approvals_run_id_kind_version"),
        ),
    )
    op.create_index(
        "ix_run_approvals_project_id_run_id",
        "run_approvals",
        ["project_id", "run_id"],
        unique=False,
    )
    op.create_index(
        "ix_run_approvals_run_id_status",
        "run_approvals",
        ["run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_run_approvals_run_id_status", table_name="run_approvals")
    op.drop_index("ix_run_approvals_project_id_run_id", table_name="run_approvals")
    op.drop_table("run_approvals")
