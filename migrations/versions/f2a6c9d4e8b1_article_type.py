"""add project article type

Revision ID: f2a6c9d4e8b1
Revises: b8e4f1c7d2a9
Create Date: 2026-10-23 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a6c9d4e8b1"
down_revision: Union[str, None] = "b8e4f1c7d2a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ARTICLE_TYPES = (
    "explainer",
    "mechanism_analysis",
    "investigative_research",
    "case_study",
    "comparative_analysis",
    "argument_critique",
    "trend_scenario",
    "decision_strategy",
    "historical_development",
)


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column(
            "article_type",
            sa.Enum(
                *_ARTICLE_TYPES,
                name="article_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="investigative_research",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("research_projects", "article_type")
