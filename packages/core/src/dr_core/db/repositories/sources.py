from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from dr_core.config import get_settings
from dr_core.db.enums import ParserStatus, SourceType, SourceVersionKind
from dr_core.db.models import DocumentChunk, Source, SourceVersion
from dr_core.db.repositories.base import Repository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.embeddings import ensure_embedding_dimension
from dr_core.normalization import (
    normalize_doi,
    normalize_url,
    text_sha256,
    url_fingerprint,
)
from dr_core.schemas import (
    DocumentChunkCreate,
    SourceCreate,
    SourceVersionCreate,
)


class SourceRepository(Repository):
    def create(self, project_id: UUID, data: SourceCreate) -> Source:
        self._require_project(project_id)
        doi = normalize_doi(data.doi) if data.doi else None
        canonical_url = normalize_url(data.url) if data.url else None
        source = Source(
            project_id=project_id,
            title=data.title,
            authors=list(data.authors),
            published_at=data.published_at,
            doi=doi,
            canonical_url=canonical_url,
            canonical_url_hash=(
                url_fingerprint(canonical_url) if canonical_url else None
            ),
            source_type=data.source_type,
            source_origin=data.source_origin,
            language=data.language,
            venue=data.venue,
            publisher=data.publisher,
            source_metadata=data.metadata,
        )
        return self._flush(source)

    def get(self, source_id: UUID, *, project_id: UUID | None = None) -> Source:
        source = self.session.get(Source, source_id)
        if source is None or (project_id is not None and source.project_id != project_id):
            raise EntityNotFound("Source not found")
        return source

    def find_by_doi(self, project_id: UUID, doi: str) -> Source | None:
        normalized = normalize_doi(doi)
        return self.session.scalar(
            select(Source).where(
                Source.project_id == project_id,
                Source.doi == normalized,
            )
        )

    def find_by_url(self, project_id: UUID, url: str) -> Source | None:
        normalized = normalize_url(url)
        return self.session.scalar(
            select(Source).where(
                Source.project_id == project_id,
                Source.canonical_url_hash == url_fingerprint(normalized),
            )
        )

    def get_or_create(self, project_id: UUID, data: SourceCreate) -> Source:
        self._require_project(project_id)
        if data.doi:
            existing = self.find_by_doi(project_id, data.doi)
            if existing is not None:
                return existing
        if data.url:
            existing = self.find_by_url(project_id, data.url)
            if existing is not None:
                return existing

        def find_existing() -> Source | None:
            if data.doi:
                by_doi = self.find_by_doi(project_id, data.doi)
                if by_doi is not None:
                    return by_doi
            if data.url:
                return self.find_by_url(project_id, data.url)
            return None

        try:
            with self.session.begin_nested():
                source = self.create(project_id, data)
                self.session.flush()
            return source
        except IntegrityError:
            existing = find_existing()
            if existing is None:
                raise
            return existing

    def list_for_project(
        self,
        project_id: UUID,
        *,
        source_type: SourceType | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Source]:
        self._require_project(project_id)
        statement = select(Source).where(Source.project_id == project_id)
        if source_type is not None:
            statement = statement.where(Source.source_type == source_type)
        statement = (
            statement.order_by(Source.created_at.asc(), Source.id.asc())
            .offset(offset)
            .limit(min(max(limit, 1), 500))
        )
        return list(self.session.scalars(statement))


class SourceVersionRepository(Repository):
    def create(
        self,
        project_id: UUID,
        source_id: UUID,
        data: SourceVersionCreate,
    ) -> SourceVersion:
        source = self.session.get(Source, source_id)
        if source is None or source.project_id != project_id:
            raise EntityNotFound("Source not found in the project")

        existing = self.find_by_content_hash(
            source_id,
            data.content_hash,
            data.content_kind,
        )
        if existing is not None:
            return existing

        latest_version = self.session.scalar(
            select(func.coalesce(func.max(SourceVersion.version_number), 0)).where(
                SourceVersion.source_id == source_id
            )
        )
        version = SourceVersion(
            project_id=project_id,
            source_id=source_id,
            version_number=int(latest_version or 0) + 1,
            content_kind=data.content_kind,
            content_hash=data.content_hash,
            storage_key=data.storage_key,
            mime_type=data.mime_type,
            size_bytes=data.size_bytes,
            fetched_url=data.fetched_url,
            license=data.license,
            parser_version=data.parser_version,
            version_metadata=data.metadata,
        )
        return self._insert_or_get(
            version,
            lambda: self.find_by_content_hash(
                source_id,
                data.content_hash,
                data.content_kind,
            ),
        )

    def find_by_content_hash(
        self,
        source_id: UUID,
        content_hash: str,
        content_kind: SourceVersionKind,
    ) -> SourceVersion | None:
        return self.session.scalar(
            select(SourceVersion).where(
                SourceVersion.source_id == source_id,
                SourceVersion.content_hash == content_hash,
                SourceVersion.content_kind == content_kind,
            )
        )

    def get(
        self,
        version_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> SourceVersion:
        version = self.session.get(SourceVersion, version_id)
        if version is None or (
            project_id is not None and version.project_id != project_id
        ):
            raise EntityNotFound("Source version not found")
        return version

    def list_for_source(
        self,
        source_id: UUID,
        *,
        project_id: UUID,
    ) -> list[SourceVersion]:
        return list(
            self.session.scalars(
                select(SourceVersion)
                .where(
                    SourceVersion.source_id == source_id,
                    SourceVersion.project_id == project_id,
                )
                .order_by(SourceVersion.version_number.desc())
            )
        )

    def list_for_project(
        self,
        project_id: UUID,
        *,
        source_ids: list[UUID] | None = None,
    ) -> list[SourceVersion]:
        statement = select(SourceVersion).where(
            SourceVersion.project_id == project_id,
        )
        if source_ids:
            statement = statement.where(SourceVersion.source_id.in_(source_ids))
        statement = statement.order_by(
            SourceVersion.source_id.asc(),
            SourceVersion.version_number.desc(),
        )
        return list(self.session.scalars(statement))

    def latest_for_source(
        self,
        source_id: UUID,
        *,
        project_id: UUID,
    ) -> SourceVersion | None:
        return self.session.scalar(
            select(SourceVersion)
            .where(
                SourceVersion.source_id == source_id,
                SourceVersion.project_id == project_id,
            )
            .order_by(SourceVersion.version_number.desc())
            .limit(1)
        )

    def mark_parsing(
        self,
        version_id: UUID,
        *,
        project_id: UUID,
        parser_version: str | None = None,
    ) -> SourceVersion:
        version = self.get(version_id, project_id=project_id)
        version.parser_status = ParserStatus.PARSING
        version.parser_version = parser_version
        version.parse_error = None
        self.session.flush()
        return version

    def mark_parsed(
        self,
        version_id: UUID,
        *,
        project_id: UUID,
        parser_version: str,
        metadata_updates: dict | None = None,
    ) -> SourceVersion:
        version = self.get(version_id, project_id=project_id)
        version.parser_status = ParserStatus.PARSED
        version.parser_version = parser_version
        version.parse_error = None
        if metadata_updates:
            version.version_metadata = {
                **version.version_metadata,
                **metadata_updates,
            }
        self.session.flush()
        return version

    def mark_parse_failed(
        self,
        version_id: UUID,
        *,
        project_id: UUID,
        error: str,
        parser_version: str | None = None,
        metadata_updates: dict | None = None,
    ) -> SourceVersion:
        version = self.get(version_id, project_id=project_id)
        if version.parser_status is ParserStatus.PARSED:
            return version
        version.parser_status = ParserStatus.FAILED
        version.parser_version = parser_version
        version.parse_error = error[:4000]
        if metadata_updates:
            version.version_metadata = {
                **version.version_metadata,
                **metadata_updates,
            }
        self.session.flush()
        return version


@dataclass(frozen=True, slots=True)
class ChunkMatch:
    chunk: DocumentChunk
    distance: float


@dataclass(frozen=True, slots=True)
class KeywordMatch:
    chunk: DocumentChunk
    fts_rank: float


class DocumentChunkRepository(Repository):
    def __init__(self, session, embedding_dimension: int | None = None) -> None:
        super().__init__(session)
        self.embedding_dimension = (
            embedding_dimension
            if embedding_dimension is not None
            else get_settings().embedding_dimension
        )

    def create_many(
        self,
        project_id: UUID,
        source_version_id: UUID,
        chunks: list[DocumentChunkCreate],
    ) -> list[DocumentChunk]:
        version = self.session.get(SourceVersion, source_version_id)
        if version is None or version.project_id != project_id:
            raise EntityNotFound("Source version not found in the project")

        entities: list[DocumentChunk] = []
        for chunk in chunks:
            embedding = None
            if chunk.embedding is not None:
                embedding = ensure_embedding_dimension(
                    chunk.embedding,
                    self.embedding_dimension,
                )
            entities.append(
                DocumentChunk(
                    project_id=project_id,
                    source_version_id=source_version_id,
                    chunk_index=chunk.chunk_index,
                    section_title=chunk.section_title,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    paragraph_start=chunk.paragraph_start,
                    paragraph_end=chunk.paragraph_end,
                    text=chunk.text,
                    text_hash=text_sha256(chunk.text),
                    token_count=chunk.token_count,
                    embedding=embedding,
                    embedding_model=chunk.embedding_model,
                )
            )

        self.session.add_all(entities)
        self.session.flush()
        return entities

    def list_for_version(
        self,
        project_id: UUID,
        source_version_id: UUID,
    ) -> list[DocumentChunk]:
        return list(
            self.session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.project_id == project_id,
                    DocumentChunk.source_version_id == source_version_id,
                )
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )

    def search_similar(
        self,
        project_id: UUID,
        embedding: list[float],
        *,
        limit: int = 10,
        source_version_id: UUID | None = None,
        source_version_ids: list[UUID] | None = None,
        source_ids: list[UUID] | None = None,
        source_types: list[SourceType] | None = None,
        published_from: date | None = None,
        published_to: date | None = None,
    ) -> list[ChunkMatch]:
        vector = ensure_embedding_dimension(embedding, self.embedding_dimension)
        distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
        statement = (
            select(DocumentChunk, distance)
            .join(
                SourceVersion,
                SourceVersion.id == DocumentChunk.source_version_id,
            )
            .join(Source, Source.id == SourceVersion.source_id)
            .where(
                DocumentChunk.project_id == project_id,
                DocumentChunk.embedding.is_not(None),
            )
        )
        if source_version_id is not None:
            statement = statement.where(
                DocumentChunk.source_version_id == source_version_id
            )
        statement = statement.where(
            *self._chunk_filters(
                source_version_ids=source_version_ids,
                source_ids=source_ids,
                source_types=source_types,
                published_from=published_from,
                published_to=published_to,
            )
        )
        rows = self.session.execute(
            statement.order_by(distance).limit(min(max(limit, 1), 100))
        ).all()
        return [
            ChunkMatch(chunk=row[0], distance=float(row[1]))
            for row in rows
        ]

    def search_keyword(
        self,
        project_id: UUID,
        query: str,
        *,
        terms: list[str] | None = None,
        limit: int = 10,
        source_version_ids: list[UUID] | None = None,
        source_ids: list[UUID] | None = None,
        source_types: list[SourceType] | None = None,
        published_from: date | None = None,
        published_to: date | None = None,
    ) -> list[KeywordMatch]:
        text = query.strip()
        if not text:
            return []
        vector = func.to_tsvector("simple", DocumentChunk.text)
        ts_query = func.websearch_to_tsquery("simple", text)
        rank = func.ts_rank_cd(vector, ts_query).label("fts_rank")
        conditions = [vector.op("@@")(ts_query)]
        safe_terms = [
            term.strip()
            for term in (terms or [])
            if 2 <= len(term.strip()) <= 64
        ][:8]
        if safe_terms:
            conditions.append(
                or_(
                    *[
                        DocumentChunk.text.icontains(term, autoescape=True)
                        for term in safe_terms
                    ]
                )
            )
        statement = (
            select(DocumentChunk, rank)
            .join(
                SourceVersion,
                SourceVersion.id == DocumentChunk.source_version_id,
            )
            .join(Source, Source.id == SourceVersion.source_id)
            .where(
                DocumentChunk.project_id == project_id,
                or_(*conditions),
                *self._chunk_filters(
                    source_version_ids=source_version_ids,
                    source_ids=source_ids,
                    source_types=source_types,
                    published_from=published_from,
                    published_to=published_to,
                ),
            )
        )
        rows = self.session.execute(
            statement.order_by(rank.desc(), DocumentChunk.id).limit(
                min(max(limit * 4, 1), 200)
            )
        ).all()
        return [
            KeywordMatch(chunk=row[0], fts_rank=float(row[1]))
            for row in rows
        ]

    @staticmethod
    def _chunk_filters(
        *,
        source_version_ids: list[UUID] | None,
        source_ids: list[UUID] | None,
        source_types: list[SourceType] | None,
        published_from: date | None,
        published_to: date | None,
    ) -> list:
        conditions = []
        if source_version_ids:
            conditions.append(
                DocumentChunk.source_version_id.in_(source_version_ids)
            )
        if source_ids:
            conditions.append(Source.id.in_(source_ids))
        if source_types:
            conditions.append(Source.source_type.in_(source_types))
        if published_from is not None:
            conditions.append(Source.published_at >= published_from)
        if published_to is not None:
            conditions.append(Source.published_at <= published_to)
        return conditions

    def set_embedding(
        self,
        chunk_id: UUID,
        embedding: list[float],
        *,
        project_id: UUID,
        embedding_model: str,
    ) -> DocumentChunk:
        vector = ensure_embedding_dimension(embedding, self.embedding_dimension)
        chunk = self.session.get(DocumentChunk, chunk_id)
        if chunk is None or chunk.project_id != project_id:
            raise EntityNotFound("Document chunk not found")
        chunk.embedding = vector
        chunk.embedding_model = embedding_model
        self.session.flush()
        return chunk
