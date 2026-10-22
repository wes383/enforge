"""Server-side research tools used by the bounded reconnaissance agent.

Tools operate on the same ingestion pipeline as the deterministic workflow:
search results are persisted before they are shown to the model, and the model
only ever sees source IDs from the database. ``read_source`` is limited to
sources this session discovered, so a tool argument cannot reach another
project's material.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from dr_core.db.enums import SourceVersionKind
from dr_core.db.repositories import SourceRepository, SourceVersionRepository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.providers.errors import ProviderError
from dr_core.research.context import ResearchContext
from dr_core.research.models import ResearchBrief, SourceRecord
from dr_core.research.source_records import (
    academic_source_record,
    merge_source_record,
    web_source_record,
)
from dr_core.services.ingestion import (
    ingest_academic_result,
    ingest_web_search_result,
)

_SEARCH_LIMIT = 5
_SNIPPET_CHARS = 600
_READABLE_KINDS = {
    SourceVersionKind.FULL_TEXT,
    SourceVersionKind.UPLOADED_DOCUMENT,
}

RESEARCH_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_sources",
            "description": (
                "Search the web or an academic index and store every result in "
                "this project's source library. Returns source IDs, titles and "
                "short excerpts. Use the returned IDs with read_source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Short search keywords, not a full sentence.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["web", "academic"],
                        "description": (
                            "web for news, reports and practice; academic for "
                            "peer-reviewed literature and preprints."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _SEARCH_LIMIT,
                    },
                },
                "required": ["query", "kind"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_source",
            "description": (
                "Read the stored title, URL and text excerpt of one source "
                "returned by search_sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "UUID from a search_sources result.",
                    }
                },
                "required": ["source_id"],
                "additionalProperties": False,
            },
        },
    },
]


class ResearchToolbox:
    def __init__(
        self,
        ctx: ResearchContext,
        brief: ResearchBrief,
        *,
        max_sources: int,
        max_searches: int | None = None,
        existing: Iterable[SourceRecord] = (),
    ) -> None:
        self._ctx = ctx
        self._brief = brief
        self._records: dict[UUID, SourceRecord] = {
            record.source_id: record for record in existing
        }
        self._max_sources = max(0, min(max_sources, brief.max_sources))
        self._max_searches = (
            ctx.settings.research_recon_max_searches
            if max_searches is None
            else max(0, max_searches)
        )
        self._max_read_chars = ctx.settings.research_recon_read_chars
        self.searches = 0
        self.reads = 0

    @property
    def records(self) -> list[SourceRecord]:
        return list(self._records.values())

    @property
    def source_count(self) -> int:
        return len(self._records)

    @property
    def max_searches(self) -> int:
        return self._max_searches

    @property
    def max_sources(self) -> int:
        return self._max_sources

    def execute(self, name: str, arguments: str) -> dict[str, Any]:
        try:
            parsed = json.loads(arguments or "{}")
        except ValueError:
            return {"error": "tool arguments must be valid JSON"}
        if not isinstance(parsed, dict):
            return {"error": "tool arguments must be a JSON object"}
        if name == "search_sources":
            return self.search_sources(
                str(parsed.get("query") or ""),
                str(parsed.get("kind") or ""),
                parsed.get("limit"),
            )
        if name == "read_source":
            return self.read_source(str(parsed.get("source_id") or ""))
        return {"error": f"unknown tool: {name}"}

    def search_sources(
        self,
        query: str,
        kind: str,
        limit: Any = None,
    ) -> dict[str, Any]:
        query = " ".join(query.split())[:500]
        if len(query) < 2:
            return {"error": "query must contain at least two characters"}
        if kind not in {"web", "academic"}:
            return {"error": "kind must be 'web' or 'academic'"}
        if self.searches >= self._max_searches:
            return {
                "error": "search budget exhausted",
                "search_calls_left": 0,
                "next": "Return the final reconnaissance JSON now.",
            }
        remaining = self._max_sources - len(self._records)
        if remaining <= 0:
            return {
                "error": "source limit reached",
                "remaining_sources": 0,
                "next": "Use read_source on existing IDs or return the final JSON.",
            }
        requested = _bounded_int(limit, _SEARCH_LIMIT, 1, _SEARCH_LIMIT)
        self.searches += 1

        provider = _first_enabled(
            self._ctx.registry.web_search
            if kind == "web"
            else self._ctx.registry.academic_search
        )
        if provider is None:
            return {"error": f"no enabled {kind} search provider"}

        try:
            if kind == "web":
                results = provider.search(query, limit=min(requested, remaining))
            else:
                results = provider.search_works(
                    query,
                    limit=min(requested, remaining),
                )
        except ProviderError as exc:
            return {
                "error": f"{exc.code}: {exc}"[:1000],
                "retryable": bool(getattr(exc, "retryable", False)),
            }

        cards: list[dict[str, Any]] = []
        for result in list(results)[: min(requested, remaining)]:
            ingested = (
                ingest_web_search_result(
                    self._ctx.database,
                    self._ctx.project_id,
                    result,
                    query=query,
                )
                if kind == "web"
                else ingest_academic_result(
                    self._ctx.database,
                    self._ctx.project_id,
                    result,
                )
            )
            record = (
                web_source_record(ingested, result, None)
                if kind == "web"
                else academic_source_record(ingested, result, None)
            )
            existing = self._records.get(record.source_id)
            self._records[record.source_id] = (
                merge_source_record(existing, record) if existing else record
            )
            cards.append(
                {
                    "source_id": str(record.source_id),
                    "title": record.title[:400],
                    "url": record.url,
                    "published_at": (
                        record.published_at.isoformat()
                        if record.published_at
                        else None
                    ),
                    "excerpt": (record.snippet or "")[:_SNIPPET_CHARS],
                }
            )

        return {
            "query": query,
            "kind": kind,
            "results": cards,
            "remaining_sources": max(0, self._max_sources - len(self._records)),
            "search_calls_left": max(0, self._max_searches - self.searches),
            "note": "search results are already stored; quote only these source IDs",
        }

    def read_source(self, source_id: str) -> dict[str, Any]:
        try:
            identifier = UUID(source_id)
        except ValueError:
            return {"error": "source_id must be a UUID returned by search_sources"}
        record = self._records.get(identifier)
        if record is None:
            return {
                "error": "source_id is unknown; call search_sources first",
            }
        self.reads += 1
        try:
            with self._ctx.database.session() as session:
                source = SourceRepository(session).get(
                    identifier,
                    project_id=self._ctx.project_id,
                )
                versions = SourceVersionRepository(session).list_for_source(
                    identifier,
                    project_id=self._ctx.project_id,
                )
                title = source.title
                url = source.canonical_url
                version = next(
                    (
                        item
                        for item in versions
                        if item.storage_key and item.content_kind in _READABLE_KINDS
                    ),
                    versions[0] if versions else None,
                )
                storage_key = version.storage_key if version else None
                mime_type = version.mime_type if version else None
                metadata = dict(version.version_metadata) if version else {}
                content_kind = version.content_kind.value if version else None
        except EntityNotFound:
            return {"error": "source not found in this project"}

        text = ""
        if storage_key and (mime_type or "").startswith("text/"):
            try:
                text = self._ctx.storage.read(storage_key).decode("utf-8-sig")
            except Exception:
                text = ""
        if not text:
            text = str(metadata.get("snippet") or record.snippet or "")
        truncated = len(text) > self._max_read_chars
        return {
            "source_id": str(identifier),
            "title": title[:400],
            "url": url,
            "content_kind": content_kind,
            "text": text[: self._max_read_chars],
            "truncated": truncated,
            "note": "excerpt only; cite via the normal evidence pipeline",
        }


def _first_enabled(providers):
    return next((provider for provider in providers if provider.enabled), None)


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))
