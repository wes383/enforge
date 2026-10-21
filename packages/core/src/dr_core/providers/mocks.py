from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, date, datetime
from typing import Sequence
from urllib.parse import urlsplit

from dr_core.db.enums import SearchProviderName
from dr_core.providers.base import (
    AcademicSearchProvider,
    DocumentParserProvider,
    LLMProvider,
    SearchProvider,
    WebContentProvider,
)
from dr_core.normalization import normalize_url
from dr_core.providers.errors import (
    DocumentParseError,
    UnsafeUrlError,
    UnsupportedDocumentType,
)
from dr_core.providers.models import (
    AcademicReference,
    AcademicResult,
    ContentProviderName,
    FetchStatus,
    LLMResponse,
    ParsedDocument,
    ParsedSection,
    ParseStatus,
    ParserName,
    SearchResult,
    WebFetchResult,
)
from dr_core.providers.text import normalize_extracted_text


class MockSearchProvider:
    name = SearchProviderName.MOCK

    def __init__(
        self,
        *,
        results: Sequence[SearchResult] | None = None,
        enabled: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self._results = list(results or [])
        self._enabled = enabled
        self._fail_with = fail_with

    @property
    def enabled(self) -> bool:
        return self._enabled

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]:
        if self._fail_with is not None:
            raise self._fail_with
        if not query.strip():
            raise ValueError("Search query must not be empty")
        results = self._results or _default_web_results(query)
        return [
            result.model_copy(update={"rank": offset + index})
            for index, result in enumerate(results[offset : offset + max(1, limit)])
        ]


class MockAcademicSearchProvider:
    name = SearchProviderName.MOCK
    supports_citations = True

    def __init__(
        self,
        *,
        results: Sequence[AcademicResult] | None = None,
        references: Sequence[AcademicReference] | None = None,
        citations: Sequence[AcademicResult] | None = None,
        enabled: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self._results = list(results or [])
        self._references = list(references or [])
        self._citations = list(citations or [])
        self._enabled = enabled
        self._fail_with = fail_with

    @property
    def enabled(self) -> bool:
        return self._enabled

    def search_works(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[AcademicResult]:
        if self._fail_with is not None:
            raise self._fail_with
        if not query.strip():
            raise ValueError("Search query must not be empty")
        results = self._results or _default_academic_results(query)
        return [
            result.model_copy(update={"rank": offset + index})
            for index, result in enumerate(results[offset : offset + max(1, limit)])
        ]

    def get_work(self, identifier: str) -> AcademicResult | None:
        if self._fail_with is not None:
            raise self._fail_with
        results = self._results or _default_academic_results(identifier)
        return results[0] if results else None

    def get_references(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicReference]:
        if self._fail_with is not None:
            raise self._fail_with
        return self._references[: max(1, limit)]

    def get_citations(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicResult]:
        if self._fail_with is not None:
            raise self._fail_with
        return self._citations[: max(1, limit)]


def validate_mock_url(url: str) -> str:
    """Validate mock URLs without DNS or network access.

    Mock providers never perform network requests; resolving example.org would
    make offline tests depend on the host resolver or an HTTP proxy.
    """
    try:
        normalized = normalize_url(url)
    except ValueError as exc:
        raise UnsafeUrlError("mock", str(exc)) from exc
    host = (urlsplit(normalized).hostname or "").casefold()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".localhost")
    ):
        raise UnsafeUrlError("mock", "Mock URL must not target an internal host")
    return normalized


class MockWebContentProvider:
    name = ContentProviderName.MOCK

    def __init__(
        self,
        *,
        status: FetchStatus = FetchStatus.SUCCESS,
        text: str | None = None,
        title: str = "Mock fetched document",
        description: str | None = "Mock document description",
        enabled: bool = True,
        fail_with: Exception | None = None,
        url_validator=None,
    ) -> None:
        self._status = status
        self._text = text
        self._title = title
        self._description = description
        self._enabled = enabled
        self._fail_with = fail_with
        self._validator = url_validator or validate_mock_url

    @property
    def enabled(self) -> bool:
        return self._enabled

    def fetch(self, url: str) -> WebFetchResult:
        if self._fail_with is not None:
            raise self._fail_with
        safe_url = self._validator(url)
        if self._status is FetchStatus.SUCCESS:
            return WebFetchResult(
                provider=self.name,
                status=self._status,
                url=safe_url,
                final_url=safe_url,
                title=self._title,
                text=self._text
                or (
                    "Mock fetched full text for offline provider tests. "
                    "This content is deterministic and requires no network access. "
                )
                * 4,
                description=self._description,
                fetched_at=datetime.now(tz=UTC),
                http_status=200,
                metadata={"mock": True},
            )
        if self._status is FetchStatus.ABSTRACT_ONLY:
            return WebFetchResult(
                provider=self.name,
                status=self._status,
                url=safe_url,
                final_url=safe_url,
                title=self._title,
                text=self._text or "Short abstract from a mock page.",
                description=self._description,
                fetched_at=datetime.now(tz=UTC),
                http_status=200,
                metadata={"mock": True},
            )
        return WebFetchResult(
            provider=self.name,
            status=self._status,
            url=safe_url,
            final_url=safe_url,
            title=self._title,
            description=self._description,
            fetched_at=datetime.now(tz=UTC),
            http_status=403 if self._status is FetchStatus.PAYWALLED else 200,
            error_code=self._status.value,
            error_message=f"Mock provider returned {self._status.value}",
            metadata={"mock": True},
        )


class MockDocumentParser:
    name = ParserName.MOCK.value
    version = "mock-parser-1.0"

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self._fail_with = fail_with

    def parse(
        self,
        content: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
    ) -> ParsedDocument:
        if self._fail_with is not None:
            raise self._fail_with
        if not content:
            raise DocumentParseError(self.name, "Document is empty")
        if mime_type == "application/pdf":
            text = "Mock parsed PDF text."
        else:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise UnsupportedDocumentType(
                    self.name,
                    "Mock parser only supports UTF-8 text",
                ) from exc
        normalized = normalize_extracted_text(text)
        if not normalized:
            raise DocumentParseError(self.name, "Document is empty")
        return ParsedDocument(
            status=ParseStatus.SUCCESS,
            title=None,
            text=normalized,
            sections=[
                ParsedSection(heading=None, level=None, paragraphs=[normalized])
            ],
            paragraph_count=1,
            character_count=len(normalized),
            parser_name=self.name,
            parser_version=self.version,
            metadata={"mock": True, "filename": filename, "mime_type": mime_type},
        )


class MockEmbeddingProvider:
    def __init__(self, *, dimension: int = 8) -> None:
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        return "mock-embedding"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            values = [0.0] * self._dimension
            for token in _tokens(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self._dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                values[index] += sign
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / norm for value in values])
        return vectors


_TOKEN_RE = re.compile(r"[0-9a-z]+|[\u3400-\u4dbf\u4e00-\u9fff]")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


class MockLLMProvider:
    name = "mock-llm"

    def __init__(self, *, text: str | None = None) -> None:
        self._text = text

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        text = self._text or f"Mock LLM response for: {prompt[:120]}"
        return LLMResponse(
            text=text,
            model=self.name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            finish_reason="stop",
        )


def _default_web_results(query: str) -> list[SearchResult]:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]
    return [
        SearchResult(
            provider=SearchProviderName.MOCK,
            title=f"Mock web result {index + 1} for {query[:80]}",
            url=f"https://example.org/mock/{digest}/{index + 1}",
            snippet=f"Deterministic mock snippet {index + 1} about {query}.",
            published_at=date(2024, 1 + index, 1),
            score=round(0.9 - index * 0.1, 2),
            rank=index,
            metadata={"mock": True},
        )
        for index in range(3)
    ]


def _default_academic_results(query: str) -> list[AcademicResult]:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]
    return [
        AcademicResult(
            provider=SearchProviderName.MOCK,
            provider_id=f"mock-paper-{index + 1}",
            title=f"Mock academic paper {index + 1}: {query[:80]}",
            authors=["Mock Author"],
            doi=f"10.5555/mock-{digest}-{index + 1}",
            url=f"https://example.org/academic/{digest}/{index + 1}",
            published_year=2023 + index,
            published_at=date(2023 + index, 6, 1),
            venue="Mock Journal",
            publisher="Mock Press",
            abstract=f"Deterministic mock abstract for {query}.",
            citation_count=10 + index,
            reference_count=5 + index,
            work_type="journal-article",
            rank=index,
            metadata={"mock": True},
        )
        for index in range(2)
    ]


__all__ = [
    "AcademicSearchProvider",
    "DocumentParserProvider",
    "LLMProvider",
    "MockAcademicSearchProvider",
    "MockDocumentParser",
    "MockEmbeddingProvider",
    "MockLLMProvider",
    "MockSearchProvider",
    "MockWebContentProvider",
    "SearchProvider",
    "WebContentProvider",
    "validate_mock_url",
]
