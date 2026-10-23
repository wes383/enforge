"""Server-side research tools used by the bounded reconnaissance agent.

Tools operate on the same ingestion pipeline as the deterministic workflow:
search results are persisted before they are shown to the model, and the model
only ever sees source IDs from the database. ``read_source`` is limited to
sources this session discovered, so a tool argument cannot reach another
project's material.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from dr_core.db.enums import ParserStatus, SourceVersionKind
from dr_core.db.repositories import SourceRepository, SourceVersionRepository
from dr_core.db.repositories.exceptions import EntityNotFound
from dr_core.providers.models import AcademicReference, AcademicResult
from dr_core.providers.orchestration import (
    collect_academic_search,
    collect_citations,
    collect_references,
    collect_web_search,
    fetch_web_content,
)
from dr_core.research.context import ResearchContext
from dr_core.research.models import ResearchBrief, SourceRecord
from dr_core.research.source_records import (
    academic_source_record,
    merge_source_record,
    source_keys,
    web_source_record,
)
from dr_core.services.ingestion import (
    ingest_academic_result,
    ingest_web_search_result,
    persist_web_fetch,
)

logger = logging.getLogger(__name__)

_SEARCH_LIMIT = 5
_SNIPPET_CHARS = 600
_READABLE_KINDS = {
    SourceVersionKind.FULL_TEXT,
    SourceVersionKind.UPLOADED_DOCUMENT,
}

_SEARCH_TOOL_SPECS: list[dict[str, Any]] = [
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

_FETCH_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "fetch_source",
        "description": (
            "Download the full page for a source returned by search_sources, "
            "store it, and return an excerpt. Use it when the snippet is too "
            "short to judge what the source says."
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
}

_CITATION_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "follow_citations",
        "description": (
            "Follow the scholarly chain around one academic source. "
            "direction=references returns the works it cites; "
            "direction=citations returns the works that cite it. Results are "
            "stored as sources and returned with their source IDs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "UUID of an academic source found in this session.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["references", "citations"],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _SEARCH_LIMIT,
                },
            },
            "required": ["source_id", "direction"],
            "additionalProperties": False,
        },
    },
}

# Reconnaissance only ever needs search/read; the research agent also gets
# full-page reading and citation chasing.
RESEARCH_TOOL_SPECS: list[dict[str, Any]] = list(_SEARCH_TOOL_SPECS)
AGENT_TOOL_SPECS: list[dict[str, Any]] = [
    *_SEARCH_TOOL_SPECS,
    _FETCH_TOOL_SPEC,
    _CITATION_TOOL_SPEC,
]


class ResearchToolbox:
    def __init__(
        self,
        ctx: ResearchContext,
        brief: ResearchBrief,
        *,
        max_sources: int,
        max_searches: int | None = None,
        max_fetches: int = 0,
        max_citation_calls: int = 0,
        max_read_chars: int | None = None,
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
        self._max_fetches = max(0, max_fetches)
        self._max_citation_calls = max(0, max_citation_calls)
        self._max_read_chars = (
            ctx.settings.research_recon_read_chars
            if max_read_chars is None
            else max(500, max_read_chars)
        )
        self.searches = 0
        self.reads = 0
        self.fetches = 0
        self.citation_calls = 0

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

    @property
    def max_fetches(self) -> int:
        return self._max_fetches

    @property
    def max_citation_calls(self) -> int:
        return self._max_citation_calls

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
        if name == "fetch_source":
            return self.fetch_source(str(parsed.get("source_id") or ""))
        if name == "follow_citations":
            return self.follow_citations(
                str(parsed.get("source_id") or ""),
                str(parsed.get("direction") or ""),
                parsed.get("limit"),
            )
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

        providers = (
            self._ctx.registry.web_search
            if kind == "web"
            else self._ctx.registry.academic_search
        )
        if _first_enabled(providers) is None:
            return {"error": f"no enabled {kind} search provider"}
        self.searches += 1

        if kind == "web":
            bundle = collect_web_search(
                providers,
                query,
                limit=min(requested, remaining),
            )
        else:
            bundle = collect_academic_search(
                providers,
                query,
                limit=min(requested, remaining),
            )
        results = bundle.results
        if not results and bundle.failures:
            return {
                "error": "search failed: "
                + "; ".join(
                    f"{failure.provider}:{failure.code}"
                    for failure in bundle.failures[:3]
                ),
                "retryable": any(
                    failure.retryable for failure in bundle.failures
                ),
            }

        cards: list[dict[str, Any]] = []
        for result in results[: min(requested, remaining)]:
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
            stored = self._store_record(record)
            cards.append(self._card(stored))

        response: dict[str, Any] = {
            "query": query,
            "kind": kind,
            "results": cards,
            "remaining_sources": max(0, self._max_sources - len(self._records)),
            "search_calls_left": max(0, self._max_searches - self.searches),
            "note": "search results are already stored; quote only these source IDs",
        }
        if bundle.failures:
            response["warnings"] = [
                f"{failure.provider}:{failure.code}"
                for failure in bundle.failures[:3]
            ]
        return response

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

    def fetch_source(self, source_id: str) -> dict[str, Any]:
        try:
            identifier = UUID(source_id)
        except ValueError:
            return {"error": "source_id must be a UUID returned by search_sources"}
        record = self._records.get(identifier)
        if record is None:
            return {"error": "source_id is unknown; call search_sources first"}
        if record.has_full_text:
            return self.read_source(source_id)
        if self.fetches >= self._max_fetches:
            return {
                "error": "fetch budget exhausted",
                "fetches_left": 0,
                "next": "Use read_source on the stored excerpt or finish.",
            }
        url = record.fetch_url or record.url
        if not url:
            return {"error": "source has no URL to fetch"}
        providers = [
            provider
            for provider in self._ctx.registry.web_content
            if provider.enabled
        ]
        if not providers:
            return {"error": "no enabled web content provider"}

        self.fetches += 1
        try:
            result, failures = fetch_web_content(providers, url)
        except Exception as exc:
            logger.warning("Source fetch tool failed for %s: %s", url, exc)
            return {"error": f"fetch failed: {type(exc).__name__}"}
        if result is None:
            return {
                "error": "fetch failed",
                "details": [
                    f"{failure.provider}:{failure.code}"
                    for failure in failures[:3]
                ],
                "fetches_left": max(0, self._max_fetches - self.fetches),
            }

        try:
            persisted = persist_web_fetch(
                self._ctx.database,
                self._ctx.project_id,
                identifier,
                result,
                storage=self._ctx.storage,
            )
        except Exception as exc:
            logger.warning("Unable to persist fetched source %s: %s", identifier, exc)
            return {"error": f"fetch could not be stored: {type(exc).__name__}"}

        if persisted.version_id:
            has_full_text = (
                persisted.content_kind is SourceVersionKind.FULL_TEXT
            )
            self._records[identifier] = record.model_copy(
                update={
                    "source_version_id": persisted.version_id,
                    "has_full_text": has_full_text,
                    "parser_status": (
                        ParserStatus.PENDING.value
                        if has_full_text
                        else ParserStatus.SKIPPED.value
                    ),
                }
            )
        text = result.text or ""
        return {
            "source_id": str(identifier),
            "status": result.status.value,
            "title": (result.title or record.title)[:400],
            "url": result.final_url or result.url,
            "text": text[: self._max_read_chars],
            "truncated": len(text) > self._max_read_chars,
            "fetches_left": max(0, self._max_fetches - self.fetches),
            "note": "stored full text; cite via the normal evidence pipeline",
        }

    def follow_citations(
        self,
        source_id: str,
        direction: str,
        limit: Any = None,
    ) -> dict[str, Any]:
        try:
            identifier = UUID(source_id)
        except ValueError:
            return {"error": "source_id must be a UUID returned by search_sources"}
        record = self._records.get(identifier)
        if record is None:
            return {"error": "source_id is unknown; call search_sources first"}
        if direction not in {"references", "citations"}:
            return {"error": "direction must be 'references' or 'citations'"}
        if not record.doi:
            return {
                "error": "source has no DOI; search for the cited work by title instead",
                "source_id": str(identifier),
            }
        if self.citation_calls >= self._max_citation_calls:
            return {
                "error": "citation budget exhausted",
                "citation_calls_left": 0,
            }
        providers = [
            provider
            for provider in self._ctx.registry.academic_search
            if provider.enabled
        ]
        if not providers:
            return {"error": "no enabled academic search provider"}
        remaining = self._max_sources - len(self._records)
        if remaining <= 0:
            return {
                "error": "source limit reached",
                "remaining_sources": 0,
            }
        requested = _bounded_int(limit, _SEARCH_LIMIT, 1, _SEARCH_LIMIT)
        self.citation_calls += 1

        if direction == "references":
            references, failures = collect_references(
                providers,
                record.doi,
                limit=requested,
            )
            results: list[AcademicResult] = []
            leads: list[str] = []
            for reference in references:
                if not any((reference.title, reference.doi, reference.url)):
                    text = " ".join((reference.unstructured or "").split())[:300]
                    if text:
                        leads.append(text)
                    continue
                try:
                    converted = _reference_to_result(
                        reference,
                        providers[0].name,
                    )
                except Exception:
                    converted = None
                if converted is not None:
                    results.append(converted)
                else:
                    text = " ".join((reference.unstructured or "").split())[:300]
                    if text:
                        leads.append(text)
        else:
            leads = []
            results, failures = collect_citations(
                providers,
                record.doi,
                limit=requested,
            )
        if not results:
            return {
                "error": f"no {direction} were found for this source",
                "details": [
                    f"{failure.provider}:{failure.code}"
                    for failure in failures[:3]
                ],
                "leads": leads[:5],
                "source_id": str(identifier),
            }

        cards: list[dict[str, Any]] = []
        for result in results[:remaining]:
            ingested = ingest_academic_result(
                self._ctx.database,
                self._ctx.project_id,
                result,
            )
            stored = self._store_record(
                academic_source_record(ingested, result, None)
            )
            card = self._card(stored)
            card["via_source_id"] = str(identifier)
            card["via_title"] = record.title[:400]
            cards.append(card)
        return {
            "from_source_id": str(identifier),
            "from_title": record.title[:400],
            "direction": direction,
            "results": cards,
            "leads": leads[:5],
            "remaining_sources": max(0, self._max_sources - len(self._records)),
            "citation_calls_left": max(
                0,
                self._max_citation_calls - self.citation_calls,
            ),
            "note": "results are already stored; quote only these source IDs",
        }

    def _store_record(self, record: SourceRecord) -> SourceRecord:
        keys = set(source_keys(record))
        for existing in self._records.values():
            if keys & set(source_keys(existing)):
                merged = merge_source_record(existing, record)
                self._records[existing.source_id] = merged
                return merged
        self._records[record.source_id] = record
        return record

    @staticmethod
    def _card(record: SourceRecord) -> dict[str, Any]:
        return {
            "source_id": str(record.source_id),
            "title": record.title[:400],
            "url": record.url,
            "doi": record.doi,
            "published_at": (
                record.published_at.isoformat()
                if record.published_at
                else None
            ),
            "has_full_text": record.has_full_text,
            "excerpt": (record.snippet or "")[:_SNIPPET_CHARS],
        }


def _reference_to_result(
    reference: AcademicReference,
    provider,
) -> AcademicResult | None:
    title = (
        reference.title
        or (reference.unstructured or "")[:500]
        or (f"DOI {reference.doi}" if reference.doi else "")
    )
    if not title:
        return None
    url = reference.url or (
        f"https://doi.org/{reference.doi}" if reference.doi else None
    )
    return AcademicResult(
        provider=provider,
        title=title[:5000],
        authors=list(reference.authors),
        doi=reference.doi,
        url=url,
        published_at=(
            date(reference.year, 1, 1) if reference.year else None
        ),
        venue=reference.venue,
    )


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
