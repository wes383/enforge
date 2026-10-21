from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from dr_core.db.enums import ParserStatus, SourceVersionKind
from dr_core.db.models import DocumentChunk, SourceVersion
from dr_core.db.repositories import DocumentChunkRepository
from dr_core.providers.models import ParsedDocument
from dr_core.research.context import ResearchContext
from dr_core.research.text import sentence_spans, tokenize
from dr_core.schemas import DocumentChunkCreate

logger = logging.getLogger(__name__)

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_MARKDOWN_TYPES = {"text/markdown", "text/x-markdown"}
_EMBED_BATCH_SIZE = 16


@dataclass(frozen=True, slots=True)
class IndexResult:
    chunk_count: int
    embedded: bool
    skipped: bool = False
    error: str | None = None


def chunk_document(
    parsed: ParsedDocument,
    *,
    mime_type: str,
    max_chars: int,
) -> list[DocumentChunkCreate]:
    chunks: list[DocumentChunkCreate] = []
    if parsed.pages and not parsed.sections:
        for page in parsed.pages:
            for text in _split_text(page.text, max_chars):
                chunks.append(
                    DocumentChunkCreate(
                        chunk_index=len(chunks),
                        page_start=page.page_number,
                        page_end=page.page_number,
                        text=text,
                        token_count=len(tokenize(text)),
                    )
                )
        return chunks

    if parsed.sections:
        paragraph_index = 0
        for section in parsed.sections:
            pending: list[str] = []
            pending_start: int | None = None
            pending_end: int | None = None
            for paragraph in section.paragraphs:
                index = paragraph_index
                paragraph_index += 1
                if not paragraph.strip():
                    continue
                parts = _split_text(paragraph, max_chars)
                if len(parts) > 1:
                    if pending:
                        chunks.append(
                            _section_chunk(
                                chunks,
                                section,
                                pending,
                                pending_start or 0,
                                pending_end or 0,
                            )
                        )
                        pending = []
                        pending_start = None
                    for part in parts:
                        chunks.append(
                            _section_chunk(
                                chunks,
                                section,
                                [part],
                                index,
                                index,
                            )
                        )
                    continue
                candidate = "\n\n".join([*pending, paragraph])
                if pending and len(candidate) > max_chars:
                    chunks.append(
                        _section_chunk(
                            chunks,
                            section,
                            pending,
                            pending_start or 0,
                            pending_end or 0,
                        )
                    )
                    pending = []
                    pending_start = None
                if pending_start is None:
                    pending_start = index
                pending.append(paragraph)
                pending_end = index
            if pending:
                chunks.append(
                    _section_chunk(
                        chunks,
                        section,
                        pending,
                        pending_start or 0,
                        pending_end or 0,
                    )
                )
        return chunks

    for text in _split_text(parsed.text, max_chars):
        chunks.append(
            DocumentChunkCreate(
                chunk_index=len(chunks),
                text=text,
                token_count=len(tokenize(text)),
            )
        )
    return chunks


def snippet_chunks(snippet: str, *, max_chars: int) -> list[DocumentChunkCreate]:
    return [
        DocumentChunkCreate(
            chunk_index=index,
            section_title="Search snippet",
            text=text,
            token_count=len(tokenize(text)),
        )
        for index, text in enumerate(_split_text(snippet, max_chars))
    ]


def chunk_locator(
    chunk: Any,
    *,
    mime_type: str | None,
    content_kind: SourceVersionKind | None,
) -> dict[str, Any]:
    if content_kind is SourceVersionKind.SEARCH_SNIPPET:
        kind = "search_snippet"
    elif (mime_type or "").lower() == "application/pdf":
        kind = "pdf"
    elif (mime_type or "").lower() in _HTML_TYPES:
        kind = "html"
    elif (mime_type or "").lower() in _MARKDOWN_TYPES:
        kind = "markdown"
    else:
        kind = "text"

    locator: dict[str, Any] = {
        "kind": kind,
        "source_version_id": str(chunk.source_version_id),
        "chunk_index": chunk.chunk_index,
    }
    chunk_id = getattr(chunk, "id", None)
    if chunk_id is not None:
        locator["chunk_id"] = str(chunk_id)
    if getattr(chunk, "section_title", None):
        locator["section"] = chunk.section_title
    if getattr(chunk, "page_start", None) is not None:
        locator["page_start"] = chunk.page_start
    if getattr(chunk, "page_end", None) is not None:
        locator["page_end"] = chunk.page_end
    if getattr(chunk, "paragraph_start", None) is not None:
        locator["paragraph_start"] = chunk.paragraph_start
    if getattr(chunk, "paragraph_end", None) is not None:
        locator["paragraph_end"] = chunk.paragraph_end
    return locator


def ensure_source_index(
    ctx: ResearchContext,
    source_version_id,
    *,
    parsed: ParsedDocument | None = None,
    parser: Any | None = None,
    snippet: str | None = None,
) -> IndexResult:
    with ctx.database.session() as session:
        existing, embedded = session.execute(
            select(
                func.count(DocumentChunk.id),
                func.count(DocumentChunk.id).filter(
                    DocumentChunk.embedding.is_not(None)
                ),
            ).where(
                DocumentChunk.project_id == ctx.project_id,
                DocumentChunk.source_version_id == source_version_id,
            )
        ).one()
        if existing:
            return IndexResult(
                chunk_count=int(existing),
                embedded=bool(embedded),
                skipped=True,
            )
        version = session.get(SourceVersion, source_version_id)
        if version is None or version.project_id != ctx.project_id:
            return IndexResult(chunk_count=0, embedded=False, skipped=True)
        content_kind = version.content_kind
        mime_type = version.mime_type
        storage_key = version.storage_key
        parser_status = version.parser_status
        metadata = dict(version.version_metadata)
        source_version_id = version.id

    error: str | None = None
    if content_kind is SourceVersionKind.SEARCH_SNIPPET:
        text = snippet or str(metadata.get("snippet") or "")
        chunks = snippet_chunks(text, max_chars=ctx.settings.research_chunk_max_chars)
    else:
        if parsed is None and storage_key and parser is not None and (
            parser_status is not ParserStatus.FAILED
        ):
            try:
                content = ctx.storage.read(storage_key)
                parsed = parser.parse(
                    content,
                    mime_type=mime_type or "application/octet-stream",
                    filename=storage_key,
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: index parse failed"
                logger.warning(
                    "Unable to parse source %s for indexing: %s",
                    source_version_id,
                    error,
                )
        if parsed is not None:
            chunks = chunk_document(
                parsed,
                mime_type=mime_type or "text/plain",
                max_chars=ctx.settings.research_chunk_max_chars,
            )
        else:
            text = snippet or str(metadata.get("snippet") or "")
            chunks = snippet_chunks(
                text,
                max_chars=ctx.settings.research_chunk_max_chars,
            )

    chunks = [chunk for chunk in chunks if chunk.text.strip()]
    if not chunks:
        return IndexResult(chunk_count=0, embedded=False, error=error)

    embedded = _attach_embeddings(ctx, chunks)
    with ctx.database.session_scope() as session:
        DocumentChunkRepository(session).create_many(
            ctx.project_id,
            source_version_id,
            chunks,
        )
    return IndexResult(
        chunk_count=len(chunks),
        embedded=embedded,
        error=error,
    )


def _attach_embeddings(
    ctx: ResearchContext,
    chunks: list[DocumentChunkCreate],
) -> bool:
    provider = ctx.registry.embedding
    if provider is None:
        return False
    expected = ctx.settings.embedding_dimension
    try:
        for start in range(0, len(chunks), _EMBED_BATCH_SIZE):
            batch = chunks[start : start + _EMBED_BATCH_SIZE]
            vectors = provider.embed_texts([chunk.text for chunk in batch])
            if len(vectors) != len(batch):
                raise ValueError("Embedding provider returned a mismatched batch")
            for chunk, vector in zip(batch, vectors, strict=True):
                if len(vector) != expected:
                    raise ValueError(
                        f"Embedding dimension {len(vector)} does not match {expected}"
                    )
                chunk.embedding = [float(value) for value in vector]
                chunk.embedding_model = provider.model_name
    except Exception as exc:
        logger.warning("Embedding generation failed; keyword search remains available: %s", exc)
        for chunk in chunks:
            chunk.embedding = None
            chunk.embedding_model = None
        return False
    return True


def _section_chunk(
    chunks: list[DocumentChunkCreate],
    section,
    paragraphs: list[str],
    paragraph_start: int,
    paragraph_end: int,
) -> DocumentChunkCreate:
    text = "\n\n".join(paragraphs).strip()
    return DocumentChunkCreate(
        chunk_index=len(chunks),
        section_title=section.heading,
        page_start=section.page_start,
        page_end=section.page_end,
        paragraph_start=max(0, paragraph_start),
        paragraph_end=max(paragraph_start, paragraph_end),
        text=text,
        token_count=len(tokenize(text)),
    )


def _split_text(text: str, max_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    max_chars = max(50, max_chars)
    if len(cleaned) <= max_chars:
        return [cleaned]
    spans = sentence_spans(cleaned)
    if not spans:
        return [
            cleaned[start : start + max_chars]
            for start in range(0, len(cleaned), max_chars)
        ]
    parts: list[str] = []
    current_start: int | None = None
    current_end: int | None = None
    for _, start, end in spans:
        if current_start is None:
            current_start, current_end = start, end
            continue
        if end - current_start > max_chars:
            parts.append(cleaned[current_start:current_end])
            current_start, current_end = start, end
        else:
            current_end = end
    if current_start is not None and current_end is not None:
        parts.append(cleaned[current_start:current_end])
        if current_end < len(cleaned):
            parts.append(cleaned[current_end:])
    covered = "".join(parts)
    if len(covered) < len(cleaned) * 0.5:
        return [
            cleaned[start : start + max_chars]
            for start in range(0, len(cleaned), max_chars)
        ]
    return [part.strip() for part in parts if part.strip()]
