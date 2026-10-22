from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from dr_core.db.enums import SearchProviderName
from dr_core.providers.models import (
    AcademicReference,
    AcademicResult,
    ContentProviderName,
    LLMResponse,
    ParsedDocument,
    SearchResult,
    WebFetchResult,
)


@runtime_checkable
class SearchProvider(Protocol):
    @property
    def name(self) -> SearchProviderName: ...

    @property
    def enabled(self) -> bool: ...

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchResult]: ...


@runtime_checkable
class AcademicSearchProvider(Protocol):
    @property
    def name(self) -> SearchProviderName: ...

    @property
    def enabled(self) -> bool: ...

    @property
    def supports_citations(self) -> bool: ...

    def search_works(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
    ) -> list[AcademicResult]: ...

    def get_work(self, identifier: str) -> AcademicResult | None: ...

    def get_references(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicReference]: ...

    def get_citations(
        self,
        identifier: str,
        *,
        limit: int = 50,
    ) -> list[AcademicResult]: ...


@runtime_checkable
class WebContentProvider(Protocol):
    @property
    def name(self) -> ContentProviderName: ...

    @property
    def enabled(self) -> bool: ...

    def fetch(self, url: str) -> WebFetchResult: ...


@runtime_checkable
class DocumentParserProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    def parse(
        self,
        content: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
    ) -> ParsedDocument: ...


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


@runtime_checkable
class ToolCallingLLM(Protocol):
    """A model that can run one turn of a tool-calling conversation."""

    @property
    def name(self) -> str: ...

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...
