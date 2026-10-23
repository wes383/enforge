"""Fill in chunk vectors that were never stored.

A chunk is written even when the embedding provider fails, so a source indexed
during an outage keeps its text but stays invisible to vector search. For a
query in another language that means the source can never become evidence: the
keyword pass shares no token with it and the vector pass has nothing to compare.

Running this after an outage heals the corpus without re-fetching or re-parsing
anything. It is idempotent: chunks that already carry a vector are skipped.

    python -m dr_core.maintenance.embeddings
    python -m dr_core.maintenance.embeddings --project-id <uuid> --limit 500
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dr_core.config import Settings, get_settings
from dr_core.db import get_database
from dr_core.db.models import DocumentChunk
from dr_core.embeddings import EmbeddingProvider, ensure_embedding_dimension
from dr_core.providers.registry import build_provider_registry

logger = logging.getLogger(__name__)

BATCH_SIZE = 16


@dataclass(frozen=True, slots=True)
class EmbeddingBackfillReport:
    scanned: int
    embedded: int
    failed: int
    batches: int
    skipped_reason: str | None = None


def backfill_missing_embeddings(
    session: Session,
    provider: EmbeddingProvider,
    *,
    expected_dimension: int,
    project_id: UUID | None = None,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
) -> EmbeddingBackfillReport:
    """Embed every chunk of the selection that has no vector yet."""
    statement = (
        select(DocumentChunk)
        .where(DocumentChunk.embedding.is_(None))
        .order_by(DocumentChunk.created_at.asc())
    )
    if project_id is not None:
        statement = statement.where(DocumentChunk.project_id == project_id)
    if limit is not None:
        statement = statement.limit(limit)
    chunks = list(session.scalars(statement))
    if not chunks:
        return EmbeddingBackfillReport(
            scanned=0,
            embedded=0,
            failed=0,
            batches=0,
            skipped_reason="no chunk is missing a vector",
        )

    embedded = 0
    failed = 0
    batches = 0
    step = max(1, batch_size)
    for start in range(0, len(chunks), step):
        batch = chunks[start : start + step]
        try:
            vectors = provider.embed_texts([chunk.text for chunk in batch])
        except Exception as exc:
            failed += len(batch)
            logger.warning(
                "Embedding batch failed at offset %s: %s: %s",
                start,
                type(exc).__name__,
                exc,
            )
            continue
        if len(vectors) != len(batch):
            failed += len(batch)
            logger.warning(
                "Embedding batch at offset %s returned %s vectors for %s chunks",
                start,
                len(vectors),
                len(batch),
            )
            continue
        for chunk, vector in zip(batch, vectors, strict=True):
            try:
                chunk.embedding = ensure_embedding_dimension(
                    vector,
                    expected_dimension,
                )
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Chunk %s kept no vector: %s: %s",
                    chunk.id,
                    type(exc).__name__,
                    exc,
                )
                continue
            chunk.embedding_model = provider.model_name
            embedded += 1
        session.flush()
        batches += 1
    return EmbeddingBackfillReport(
        scanned=len(chunks),
        embedded=embedded,
        failed=failed,
        batches=batches,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m dr_core.maintenance.embeddings",
        description="Embed document chunks that have no vector yet.",
    )
    parser.add_argument("--project-id", type=UUID, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings: Settings = get_settings()
    registry = build_provider_registry(settings, use_mocks=False)
    try:
        provider = registry.embedding
        if provider is None:
            print(
                json.dumps(
                    {
                        "error": (
                            "No embedding provider is configured; set "
                            "EMBEDDING_ENABLED and EMBEDDING_API_KEY."
                        )
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        with get_database().session_scope() as session:
            report = backfill_missing_embeddings(
                session,
                provider,
                expected_dimension=settings.embedding_dimension,
                project_id=args.project_id,
                limit=args.limit,
                batch_size=args.batch_size,
            )
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        return 0 if report.failed == 0 else 1
    finally:
        registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
