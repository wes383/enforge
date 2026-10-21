"""phase 6 chunk full-text index

Revision ID: a7d3e5f9c2b4
Revises: e5b9c3d7a1f2
Create Date: 2026-10-21 06:10:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7d3e5f9c2b4"
down_revision: Union[str, None] = "e5b9c3d7a1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_document_chunks_text_fts "
        "ON document_chunks USING gin (to_tsvector('simple', text))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_text_fts")
