"""Evaluation providers that replay a frozen document corpus.

The deterministic mock providers answer with placeholder sentences, so every
quality metric measured on them describes string matching rather than writing.
This module serves documents that look like what a real content provider
returns — several paragraphs, concrete numbers, sources that disagree — and
runs the production parser and chunker over them.

The corpus is declared per case in ``sources.json``. Document bodies live next
to it, so replacing a file is how a real document enters the suite.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from dr_core.config import Settings
from dr_core.db.enums import SearchProviderName
from dr_core.evaluation.cases import EvaluationCase
from dr_core.providers.base import LLMProvider
from dr_core.providers.errors import ProviderResourceNotFound
from dr_core.providers.mocks import (
    MockAcademicSearchProvider,
    MockEmbeddingProvider,
    MockLLMProvider,
    validate_mock_url,
)
from dr_core.providers.models import (
    AcademicResult,
    ContentProviderName,
    FetchStatus,
    SearchResult,
    WebFetchResult,
)
from dr_core.providers.parsing import LocalDocumentParser
from dr_core.providers.registry import ProviderRegistry
from dr_core.providers.text import normalize_extracted_text

DEFAULT_CORPUS_DIR = Path("tests/evaluation/corpus")
_MIN_SCORE = 0.1


@dataclass(frozen=True, slots=True)
class CorpusSource:
    id: str
    kind: str
    url: str
    title: str
    snippet: str
    body: str
    authors: tuple[str, ...] = ()
    published_at: date | None = None
    venue: str | None = None
    doi: str | None = None
    abstract: str = ""

    @property
    def searchable(self) -> str:
        return f"{self.title} {self.snippet} {self.abstract}".casefold()


@dataclass(frozen=True, slots=True)
class CorpusBundle:
    case_id: str
    notes: str
    sources: tuple[CorpusSource, ...] = ()

    @property
    def web_sources(self) -> tuple[CorpusSource, ...]:
        return tuple(item for item in self.sources if item.kind == "web")

    @property
    def academic_sources(self) -> tuple[CorpusSource, ...]:
        return tuple(item for item in self.sources if item.kind == "academic")

    @property
    def documents(self) -> dict[str, str]:
        return {item.url: item.body for item in self.sources}


def corpus_root() -> Path:
    return Path(os.environ.get("EVALUATION_CORPUS_DIR", str(DEFAULT_CORPUS_DIR)))


def load_corpus(case_id: str) -> CorpusBundle:
    directory = corpus_root() / case_id
    manifest_path = directory / "sources.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Evaluation corpus for {case_id!r} is missing: {manifest_path}"
        )
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources: list[CorpusSource] = []
    for entry in manifest.get("sources") or []:
        body_path = directory / str(entry["body"])
        body = normalize_extracted_text(body_path.read_text(encoding="utf-8"))
        if not body:
            raise ValueError(f"Corpus document {body_path} has no text")
        sources.append(
            CorpusSource(
                id=str(entry["id"]),
                kind=str(entry.get("kind") or "web"),
                url=str(entry["url"]),
                title=str(entry["title"]),
                snippet=str(entry.get("snippet") or body[:240]),
                body=body,
                authors=tuple(str(author) for author in entry.get("authors") or ()),
                published_at=_date(entry.get("published_at")),
                venue=entry.get("venue"),
                doi=entry.get("doi"),
                abstract=str(entry.get("abstract") or ""),
            )
        )
    return CorpusBundle(
        case_id=case_id,
        notes=str(manifest.get("notes") or ""),
        sources=tuple(sources),
    )


class CorpusSearchProvider:
    """Returns corpus documents ordered by how many query terms they share."""

    name = SearchProviderName.MOCK

    def __init__(self, sources: tuple[CorpusSource, ...]) -> None:
        self._sources = sources

    @property
    def enabled(self) -> bool:
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        terms = _terms(query)
        scored: list[tuple[int, int, CorpusSource]] = []
        for index, source in enumerate(self._sources):
            haystack = source.searchable
            overlap = sum(1 for term in terms if term in haystack)
            scored.append((-overlap, index, source))
        scored.sort()
        selected = scored[offset : offset + max(1, limit)]
        results: list[SearchResult] = []
        for rank, (negative, _index, source) in enumerate(selected):
            overlap = -negative
            score = round(min(0.95, 0.35 + 0.15 * overlap), 2)
            results.append(
                SearchResult(
                    provider=self.name,
                    title=source.title,
                    url=source.url,
                    snippet=source.snippet,
                    published_at=source.published_at,
                    score=max(score, _MIN_SCORE),
                    rank=rank,
                    metadata={"evaluation": True, "corpus": True},
                )
            )
        return results


class CorpusContentProvider:
    """Serves the frozen body of each corpus document."""

    name = ContentProviderName.MOCK

    def __init__(self, bundle: CorpusBundle) -> None:
        self._bundle = bundle
        self._documents = bundle.documents
        self._titles = {item.url: item.title for item in bundle.sources}

    @property
    def enabled(self) -> bool:
        return True

    def fetch(self, url: str) -> WebFetchResult:
        safe_url = validate_mock_url(url)
        text = self._documents.get(url) or self._documents.get(safe_url)
        if text is None:
            raise ProviderResourceNotFound(
                self.name.value,
                f"Corpus has no document for {safe_url}",
            )
        return WebFetchResult(
            provider=self.name,
            status=FetchStatus.SUCCESS,
            url=safe_url,
            final_url=safe_url,
            title=self._titles.get(url) or self._titles.get(safe_url),
            text=text,
            description=text[:240],
            fetched_at=datetime.now(tz=UTC),
            http_status=200,
            metadata={"evaluation": True, "corpus": True},
        )


def build_corpus_registry(
    case: EvaluationCase,
    settings: Settings,
    *,
    llm: LLMProvider | None = None,
) -> ProviderRegistry:
    bundle = load_corpus(case.id)
    academic = [
        AcademicResult(
            provider=SearchProviderName.MOCK,
            provider_id=f"corpus-{source.id}",
            title=source.title,
            authors=list(source.authors),
            doi=source.doi,
            url=source.url,
            published_year=source.published_at.year if source.published_at else None,
            published_at=source.published_at,
            venue=source.venue,
            publisher=source.venue,
            abstract=source.abstract or source.body[:2000],
            citation_count=10,
            work_type="journal-article",
            rank=index,
            metadata={"evaluation": True, "corpus": True},
        )
        for index, source in enumerate(bundle.academic_sources)
    ]
    return ProviderRegistry(
        web_search=[CorpusSearchProvider(bundle.web_sources)],
        # An empty result list would make the mock provider fall back to its
        # generated sample, so the provider is only registered when the corpus
        # actually declares academic sources.
        academic_search=(
            [MockAcademicSearchProvider(results=academic)] if academic else []
        ),
        web_content=[CorpusContentProvider(bundle)],
        parsers=[
            LocalDocumentParser(
                max_input_bytes=settings.parser_max_input_bytes,
                max_output_chars=settings.parser_max_output_chars,
                max_pdf_pages=settings.parser_max_pdf_pages,
            )
        ],
        embedding=MockEmbeddingProvider(dimension=settings.embedding_dimension),
        llm=llm or MockLLMProvider(),
    )


def _terms(query: str) -> list[str]:
    return [
        token
        for token in normalize_extracted_text(query).casefold().split()
        if len(token) > 3
    ]


def _date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "CorpusBundle",
    "CorpusContentProvider",
    "CorpusSearchProvider",
    "CorpusSource",
    "build_corpus_registry",
    "corpus_root",
    "load_corpus",
]
