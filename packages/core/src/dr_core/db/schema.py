from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dr_core.config import get_settings
from dr_core.db.session import get_database

logger = logging.getLogger(__name__)

_COLUMN_TYPE_QUERY = (
    "SELECT format_type(a.atttypid, a.atttypmod) "
    "FROM pg_attribute a "
    "JOIN pg_class c ON c.oid = a.attrelid "
    "WHERE c.relname = 'document_chunks' "
    "AND a.attname = 'embedding' "
    "AND a.attnum > 0 AND NOT a.attisdropped"
)

_EMBEDDING_INDEX = "ix_document_chunks_embedding_hnsw"


def parse_vector_dimension(column_type: str | None) -> int | None:
    """Return the dimension of a pgvector column type.

    ``vector(1536)`` yields 1536, and a typmod-free ``vector`` yields None
    because such a column accepts any dimension.
    """
    if not column_type:
        return None
    text = str(column_type).strip()
    if not text.startswith("vector") or "(" not in text:
        return None
    return int(text.removeprefix("vector(").rstrip(")"))


def embedding_column_dimension(session: Session) -> int | None:
    """Return the dimension of the chunk vector column in the database."""
    column_type = session.execute(sa.text(_COLUMN_TYPE_QUERY)).scalar()
    return parse_vector_dimension(column_type)


def embedding_dimension_mismatch(session: Session, dimension: int) -> str | None:
    """Describe why the column cannot store vectors of the given dimension."""
    column_dimension = embedding_column_dimension(session)
    if column_dimension is None or column_dimension == int(dimension):
        return None
    return (
        f"The chunk vector column is vector({column_dimension}) but "
        f"EMBEDDING_DIMENSION is {int(dimension)}. Run the alignment step "
        "(python -m dr_core.maintenance.alignment) and index the documents again."
    )


def align_embedding_column(session: Session, dimension: int) -> dict[str, object]:
    """Make the chunk vector column match the configured dimension.

    Vectors of the previous dimension cannot be cast and belong to a model
    that is no longer configured, so they are dropped and rebuilt by the next
    indexing pass.
    """
    target = int(dimension)
    current = embedding_column_dimension(session)
    if current == target:
        return {"action": "unchanged", "dimension": target, "previous": current}
    session.execute(sa.text(f"DROP INDEX IF EXISTS {_EMBEDDING_INDEX}"))
    session.execute(
        sa.text(
            "UPDATE document_chunks "
            "SET embedding = NULL, embedding_model = NULL "
            "WHERE embedding IS NOT NULL"
        )
    )
    session.execute(
        sa.text(
            "ALTER TABLE document_chunks "
            f"ALTER COLUMN embedding TYPE vector({target})"
        )
    )
    session.execute(
        sa.text(
            f"CREATE INDEX {_EMBEDDING_INDEX} ON document_chunks "
            "USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
    )
    return {"action": "altered", "dimension": target, "previous": current}


def log_embedding_dimension_mismatch() -> None:
    """Report a column that cannot store vectors of the configured dimension."""
    settings = get_settings()
    if not settings.embedding_enabled:
        return
    try:
        with get_database().session() as session:
            mismatch = embedding_dimension_mismatch(
                session,
                settings.embedding_dimension,
            )
    except Exception as exc:
        logger.warning("Unable to verify the chunk vector column: %s", exc)
        return
    if mismatch:
        logger.error("%s", mismatch)
