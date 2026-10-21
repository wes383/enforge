"""source version kind dedup

Revision ID: 4f6d1c8a2b90
Revises: c991dd5b285d
Create Date: 2026-10-21 02:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4f6d1c8a2b90"
down_revision: Union[str, None] = "c991dd5b285d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_source_versions_source_id_content_hash",
        "source_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_versions_source_id_content_hash_content_kind",
        "source_versions",
        ["source_id", "content_hash", "content_kind"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_source_versions_source_id_content_hash_content_kind",
        "source_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_versions_source_id_content_hash",
        "source_versions",
        ["source_id", "content_hash"],
    )
