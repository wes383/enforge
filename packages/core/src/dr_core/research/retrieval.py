from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from dr_core.config import Settings
from dr_core.db.enums import SourceOrigin, SourceType
from dr_core.db.models import EvidenceItem, Source, SourceVersion
from dr_core.db.repositories.sources import DocumentChunkRepository
from dr_core.embeddings import EmbeddingProvider
from dr_core.research.indexing import chunk_locator
from dr_core.research.text import tokenize
from dr_core.schemas.base import SchemaModel

_RRF_K = 60


class RetrievalFilters(SchemaModel):
    source_types: list[SourceType] = Field(default_factory=list, max_length=20)
    published_from: date | None = None
    published_to: date | None = None
    source_ids: list[UUID] = Field(default_factory=list, max_length=200)
    source_version_ids: list[UUID] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _ordered_dates(self) -> "RetrievalFilters":
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_to < self.published_from
        ):
            raise ValueError("published_to must not be before published_from")
        return self


class RetrievalHit(SchemaModel):
    chunk_id: UUID
    source_id: UUID
    source_version_id: UUID
    text: str = Field(min_length=1)
    score: float = Field(ge=0)
    keyword_score: float = Field(default=0.0, ge=0, le=1)
    vector_score: float | None = Field(default=None, ge=0, le=1)
    keyword_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    keyword_matched: bool = False
    vector_matched: bool = False
    source_title: str = Field(min_length=1)
    source_type: SourceType = SourceType.OTHER
    source_origin: SourceOrigin = SourceOrigin.MANUAL
    published_at: date | None = None
    doi: str | None = None
    url: str | None = None
    authors: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[UUID] = Field(default_factory=list)


class HybridSearchResult(SchemaModel):
    hits: list[RetrievalHit] = Field(default_factory=list)
    keyword_used: bool = False
    vector_used: bool = False
    degraded_reason: str | None = None


class HybridRetriever:
    def __init__(
        self,
        session: Session,
        project_id: UUID,
        *,
        settings: Settings,
        embedding: EmbeddingProvider | None = None,
        run_id: UUID | None = None,
    ) -> None:
        self.session = session
        self.project_id = project_id
        self.settings = settings
        self.embedding = embedding
        self.run_id = run_id
        self.repository = DocumentChunkRepository(session)

    def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int | None = None,
    ) -> HybridSearchResult:
        filters = filters or RetrievalFilters()
        limit = min(max(limit or self.settings.research_retrieval_limit, 1), 100)
        mode = self.settings.research_retrieval_mode
        keyword_matches = []
        vector_matches = []
        degraded_reason: str | None = None
        keyword_used = mode in {"hybrid", "keyword"}

        if keyword_used:
            keyword_matches = self._keyword_matches(query, filters, limit)

        if mode in {"hybrid", "vector"}:
            if self.embedding is None:
                degraded_reason = "No embedding provider is configured; using keyword search."
            else:
                try:
                    vector = self.embedding.embed_texts([query])[0]
                    vector_matches = self.repository.search_similar(
                        self.project_id,
                        vector,
                        limit=limit,
                        source_version_ids=filters.source_version_ids or None,
                        source_ids=filters.source_ids or None,
                        source_types=filters.source_types or None,
                        published_from=filters.published_from,
                        published_to=filters.published_to,
                    )
                except Exception as exc:
                    degraded_reason = (
                        f"Vector search unavailable ({type(exc).__name__}); "
                        "using keyword search."
                    )
            if mode == "vector" and degraded_reason:
                keyword_matches = self._keyword_matches(query, filters, limit)
                keyword_used = True

        query_tokens = tokenize(query)
        keyword_rows = []
        for match in keyword_matches:
            chunk_tokens = tokenize(match.chunk.text)
            lexical = (
                len(query_tokens & chunk_tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            keyword_rows.append((match, lexical))
        keyword_rows.sort(
            key=lambda item: (-item[1], -item[0].fts_rank, item[0].chunk.chunk_index)
        )

        scores: dict[UUID, float] = defaultdict(float)
        keyword_rank: dict[UUID, int] = {}
        vector_rank: dict[UUID, int] = {}
        keyword_score: dict[UUID, float] = {}
        vector_score: dict[UUID, float] = {}
        chunks = {}

        for rank, (match, lexical) in enumerate(keyword_rows, start=1):
            chunk_id = match.chunk.id
            chunks[chunk_id] = match.chunk
            keyword_rank[chunk_id] = rank
            keyword_score[chunk_id] = round(
                min(1.0, 0.8 * lexical + 0.2 * min(1.0, match.fts_rank)),
                6,
            )
            scores[chunk_id] += 1.0 / (_RRF_K + rank)

        for rank, match in enumerate(vector_matches, start=1):
            chunk_id = match.chunk.id
            chunks[chunk_id] = match.chunk
            vector_rank[chunk_id] = rank
            vector_score[chunk_id] = round(
                min(1.0, max(0.0, 1.0 - match.distance)),
                6,
            )
            scores[chunk_id] += 1.0 / (_RRF_K + rank)

        ranked = sorted(
            scores,
            key=lambda chunk_id: (
                -scores[chunk_id],
                -keyword_score.get(chunk_id, 0.0),
                -vector_score.get(chunk_id, 0.0),
                chunks[chunk_id].chunk_index,
            ),
        )[:limit]
        if not ranked:
            return HybridSearchResult(
                keyword_used=keyword_used,
                vector_used=bool(vector_matches),
                degraded_reason=degraded_reason,
            )

        versions = {
            version.id: version
            for version in self.session.scalars(
                select(SourceVersion).where(
                    SourceVersion.id.in_(
                        {chunks[chunk_id].source_version_id for chunk_id in ranked}
                    )
                )
            )
        }
        sources = {
            source.id: source
            for source in self.session.scalars(
                select(Source).where(
                    Source.id.in_(
                        {
                            version.source_id
                            for version in versions.values()
                            if version is not None
                        }
                    )
                )
            )
        }
        evidence_ids = self._evidence_ids([str(chunk_id) for chunk_id in ranked])

        hits: list[RetrievalHit] = []
        for chunk_id in ranked:
            chunk = chunks[chunk_id]
            version = versions.get(chunk.source_version_id)
            source = sources.get(version.source_id) if version is not None else None
            if source is None or version is None:
                continue
            locator = chunk_locator(
                chunk,
                mime_type=version.mime_type,
                content_kind=version.content_kind,
            )
            hits.append(
                RetrievalHit(
                    chunk_id=chunk.id,
                    source_id=source.id,
                    source_version_id=version.id,
                    text=chunk.text,
                    score=round(scores[chunk_id], 8),
                    keyword_score=keyword_score.get(chunk_id, 0.0),
                    vector_score=vector_score.get(chunk_id),
                    keyword_rank=keyword_rank.get(chunk_id),
                    vector_rank=vector_rank.get(chunk_id),
                    keyword_matched=chunk_id in keyword_rank,
                    vector_matched=chunk_id in vector_rank,
                    source_title=source.title,
                    source_type=source.source_type,
                    source_origin=source.source_origin,
                    published_at=source.published_at,
                    doi=source.doi,
                    url=source.canonical_url,
                    authors=list(source.authors or []),
                    locator=locator,
                    evidence_ids=evidence_ids.get(str(chunk.id), []),
                )
            )
        return HybridSearchResult(
            hits=hits,
            keyword_used=keyword_used,
            vector_used=bool(vector_matches),
            degraded_reason=degraded_reason,
        )

    def _keyword_matches(self, query: str, filters: RetrievalFilters, limit: int):
        return self.repository.search_keyword(
            self.project_id,
            query,
            terms=sorted(tokenize(query)),
            limit=limit,
            source_version_ids=filters.source_version_ids or None,
            source_ids=filters.source_ids or None,
            source_types=filters.source_types or None,
            published_from=filters.published_from,
            published_to=filters.published_to,
        )

    def _evidence_ids(self, chunk_ids: list[str]) -> dict[str, list[UUID]]:
        if not chunk_ids:
            return {}
        statement = select(EvidenceItem.id, EvidenceItem.locator).where(
            EvidenceItem.project_id == self.project_id,
            EvidenceItem.locator["chunk_id"].astext.in_(chunk_ids),
        )
        if self.run_id is not None:
            statement = statement.where(EvidenceItem.run_id == self.run_id)
        result: dict[str, list[UUID]] = defaultdict(list)
        for evidence_id, locator in self.session.execute(statement):
            chunk_id = (locator or {}).get("chunk_id")
            if chunk_id:
                result[str(chunk_id)].append(evidence_id)
        return result
