"""Bounded supplemental retrieval while a section is being written.

The writing model may ask for extra sources when the evidence it received is
insufficient. Requests are executed through the same research toolbox used by
reconnaissance, so results are persisted before any evidence is created, and
the model only sees the resulting evidence markers. One supplement round is
allowed per section and the whole run shares fixed search/source/evidence caps.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from dr_core.db.repositories import EvidenceRepository
from dr_core.research.context import ResearchContext
from dr_core.research.evidence_records import evidence_from_text
from dr_core.research.indexing import ensure_source_index
from dr_core.research.models import OutlineSection, ResearchBrief, SourceRecord
from dr_core.research.reporting import source_views
from dr_core.research.text import best_sentence_span
from dr_core.research.tools import ResearchToolbox
from dr_core.research.writing import WriteSearchOutcome, WriteSearchRequest

logger = logging.getLogger(__name__)

_PER_QUERY_LIMIT = 3


class WriteSearchSession:
    def __init__(
        self,
        ctx: ResearchContext,
        brief: ResearchBrief,
        *,
        parser: Any,
        numbering: dict,
        sources: dict,
        existing: list[SourceRecord],
        evidence_count: int,
    ) -> None:
        self._ctx = ctx
        self._parser = parser
        self.numbering = numbering
        self.sources = sources
        self._toolbox = ResearchToolbox(
            ctx,
            brief,
            max_sources=min(
                brief.max_sources,
                len(existing) + ctx.settings.research_write_search_max_sources,
            ),
            max_searches=ctx.settings.research_write_search_max_searches,
            existing=existing,
        )
        self._max_queries = (
            ctx.settings.research_write_search_max_requests_per_section
        )
        self._evidence_left = max(
            0,
            min(
                ctx.settings.research_write_search_max_evidence,
                brief.max_evidence - evidence_count,
            ),
        )
        with ctx.database.session() as session:
            self._evidence_ids = {
                item.id
                for item in EvidenceRepository(session).list_for_run(
                    ctx.project_id,
                    ctx.run_id,
                )
            }

    def supplement(
        self,
        section: OutlineSection,
        requests: list[WriteSearchRequest],
    ) -> WriteSearchOutcome:
        requests = requests[: self._max_queries]
        if not requests:
            return WriteSearchOutcome()
        if self._toolbox.searches >= self._toolbox.max_searches:
            return WriteSearchOutcome(
                errors=("write-time search budget exhausted",),
            )
        if self._toolbox.source_count >= self._toolbox.max_sources:
            return WriteSearchOutcome(
                errors=("write-time source limit reached",),
            )
        if self._evidence_left <= 0:
            return WriteSearchOutcome(errors=("evidence budget exhausted",))

        searches_before = self._toolbox.searches

        errors: list[str] = []
        queries: list[str] = []
        created: list[Any] = []
        added_sources = 0
        for request in requests:
            if self._toolbox.searches >= self._toolbox.max_searches:
                errors.append("write-time search budget exhausted")
                break
            if self._toolbox.source_count >= self._toolbox.max_sources:
                errors.append("write-time source limit reached")
                break
            before = {record.source_id for record in self._toolbox.records}
            result = self._toolbox.search_sources(
                request.query,
                request.kind,
                limit=_PER_QUERY_LIMIT,
            )
            if result.get("error"):
                errors.append(str(result["error"])[:500])
                continue
            queries.append(request.query)
            returned_ids = [
                UUID(str(card.get("source_id")))
                for card in result.get("results", [])
                if card.get("source_id")
            ]
            records_by_id = {
                record.source_id: record for record in self._toolbox.records
            }
            candidates = [
                records_by_id[source_id]
                for source_id in returned_ids
                if source_id in records_by_id
            ]
            added_sources += sum(
                1 for record in candidates if record.source_id not in before
            )
            for record in candidates:
                if self._evidence_left <= 0:
                    break
                created.extend(self._extract(section, record, request.query))

        if created:
            with self._ctx.database.session() as session:
                self.sources.update(source_views(session, self._ctx.project_id))
        return WriteSearchOutcome(
            evidence=created,
            sources_added=added_sources,
            evidence_added=len(created),
            searches=self._toolbox.searches - searches_before,
            queries=tuple(queries),
            errors=tuple(errors),
        )

    def _extract(
        self,
        section: OutlineSection,
        record: SourceRecord,
        query: str,
    ) -> list[Any]:
        if record.source_version_id is None:
            return []
        try:
            ensure_source_index(
                self._ctx,
                record.source_version_id,
                parser=self._parser,
                snippet=record.snippet,
            )
        except Exception as exc:
            logger.warning(
                "Unable to index supplemental source %s: %s",
                record.source_id,
                exc,
            )
        text = record.snippet or ""
        match = _best_match(text, section, query)
        if match is None:
            return []
        sentence, _, start, end = match
        with self._ctx.database.session_scope() as session:
            item = EvidenceRepository(session).create(
                self._ctx.project_id,
                self._ctx.run_id,
                evidence_from_text(
                    record,
                    section_id=section.section_id,
                    text=text,
                    sentence=sentence,
                    start=start,
                    end=end,
                    kind="search_snippet",
                ),
            )
        if item.id in self._evidence_ids:
            return []
        self._evidence_ids.add(item.id)
        self._evidence_left -= 1
        if item.source_id not in self.numbering:
            self.numbering[item.source_id] = (
                max(self.numbering.values(), default=0) + 1
            )
        return [item]


def _best_match(
    text: str,
    section: OutlineSection,
    query: str,
) -> tuple[str, int, int, int] | None:
    queries = [query]
    fallback = " ".join([section.title, *section.key_claims]).strip()
    if fallback and fallback != query:
        queries.append(fallback)
    for candidate in queries:
        matches = best_sentence_span(text, candidate, limit=1)
        if matches:
            return matches[0]
    return None
