"""phase 8 project research options

Revision ID: b8e4f1c7d2a9
Revises: a7d3e5f9c2b4
Create Date: 2026-10-21 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f1c7d2a9"
down_revision: Union[str, None] = "a7d3e5f9c2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column("target_words", sa.Integer(), nullable=True),
    )
    op.add_column(
        "research_projects",
        sa.Column("date_from", sa.Date(), nullable=True),
    )
    op.add_column(
        "research_projects",
        sa.Column("date_to", sa.Date(), nullable=True),
    )
    op.create_check_constraint(
        "target_words_range",
        "research_projects",
        "target_words IS NULL OR (target_words >= 100 AND target_words <= 50000)",
    )
    op.create_check_constraint(
        "project_date_range_ordered",
        "research_projects",
        "date_from IS NULL OR date_to IS NULL OR date_to >= date_from",
    )


def downgrade() -> None:
    op.drop_constraint(
        "project_date_range_ordered",
        "research_projects",
        type_="check",
    )
    op.drop_constraint(
        "target_words_range",
        "research_projects",
        type_="check",
    )
    op.drop_column("research_projects", "date_to")
    op.drop_column("research_projects", "date_from")
    op.drop_column("research_projects", "target_words")
