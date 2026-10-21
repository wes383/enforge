from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from dr_core.db.enums import SearchProviderName
from dr_core.normalization import (
    InvalidDOI,
    InvalidURL,
    normalize_doi,
    normalize_url,
    text_sha256,
)
from dr_core.schemas.base import SchemaModel


class ContentProviderName(StrEnum):
    FIRECRAWL = "firecrawl"
    MOCK = "mock"


class ParserName(StrEnum):
    LOCAL = "local"
    MOCK = "mock"


class FetchStatus(StrEnum):
    SUCCESS = "success"
    ABSTRACT_ONLY = "abstract_only"
    PAYWALLED = "paywalled"
    FAILED = "failed"
    BLOCKED = "blocked"
    TOO_LARGE = "too_large"


class ParseStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"


def _normalized_url(value: str) -> str:
    try:
        return normalize_url(value)
    except InvalidURL as exc:
        raise ValueError(str(exc)) from exc


def _normalized_doi(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_doi(value)
    except InvalidDOI as exc:
        raise ValueError(str(exc)) from exc


class ProviderFailure(SchemaModel):
    provider: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    retry_after_seconds: float | None = Field(default=None, ge=0)
    status_code: int | None = Field(default=None, ge=0, le=599)


class SearchResult(SchemaModel):
    provider: SearchProviderName
    title: str = Field(min_length=1, max_length=5000)
    url: str = Field(min_length=1, max_length=4096)
    snippet: str = Field(default="", max_length=20000)
    published_at: date | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    rank: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _normalized_url(value)


class AcademicResult(SchemaModel):
    provider: SearchProviderName
    provider_id: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=5000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    doi: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=4096)
    published_year: int | None = Field(default=None, ge=1000, le=3000)
    published_at: date | None = None
    venue: str | None = Field(default=None, max_length=1000)
    publisher: str | None = Field(default=None, max_length=1000)
    abstract: str | None = Field(default=None, max_length=100000)
    citation_count: int | None = Field(default=None, ge=0)
    reference_count: int | None = Field(default=None, ge=0)
    open_access_url: str | None = Field(default=None, max_length=4096)
    work_type: str | None = Field(default=None, max_length=128)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    rank: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        return _normalized_doi(value)

    @field_validator("url", "open_access_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _normalized_url(value) if value else None

    @model_validator(mode="after")
    def _derive_year(self) -> "AcademicResult":
        if self.published_year is None and self.published_at is not None:
            self.published_year = self.published_at.year
        return self


class AcademicReference(SchemaModel):
    title: str | None = Field(default=None, max_length=5000)
    authors: list[str] = Field(default_factory=list, max_length=200)
    doi: str | None = Field(default=None, max_length=255)
    year: int | None = Field(default=None, ge=1000, le=3000)
    venue: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=4096)
    unstructured: str | None = Field(default=None, max_length=10000)

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        return _normalized_doi(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _normalized_url(value) if value else None

    @model_validator(mode="after")
    def _require_identity(self) -> "AcademicReference":
        if not any((self.title, self.doi, self.unstructured)):
            raise ValueError("A reference requires a title, DOI, or unstructured text")
        return self


class WebFetchResult(SchemaModel):
    provider: ContentProviderName
    status: FetchStatus
    url: str = Field(min_length=1, max_length=4096)
    final_url: str | None = Field(default=None, max_length=4096)
    title: str | None = Field(default=None, max_length=5000)
    text: str = ""
    description: str | None = Field(default=None, max_length=20000)
    fetched_at: datetime
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    http_status: int | None = Field(default=None, ge=0, le=599)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url", "final_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _normalized_url(value) if value else None

    @field_validator("fetched_at")
    @classmethod
    def _aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fetched_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> "WebFetchResult":
        if self.status is FetchStatus.SUCCESS and not self.text.strip():
            raise ValueError("Successful fetches require non-empty text")
        if self.status in {FetchStatus.SUCCESS, FetchStatus.ABSTRACT_ONLY}:
            if not self.text.strip() and not (self.description or "").strip():
                raise ValueError("Fetched content requires text or a description")
        if self.status in {
            FetchStatus.FAILED,
            FetchStatus.PAYWALLED,
            FetchStatus.BLOCKED,
            FetchStatus.TOO_LARGE,
        } and not (self.error_message or "").strip():
            raise ValueError("Unsuccessful fetches require an error message")
        if self.text:
            expected = text_sha256(self.text)
            if self.content_hash is None:
                self.content_hash = expected
            elif self.content_hash != expected:
                raise ValueError("content_hash does not match the fetched text")
        return self


class ParsedSection(SchemaModel):
    heading: str | None = Field(default=None, max_length=2000)
    level: int | None = Field(default=None, ge=1, le=6)
    paragraphs: list[str] = Field(default_factory=list)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _range(self) -> "ParsedSection":
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end must not be before page_start")
        return self


class ParsedPage(SchemaModel):
    page_number: int = Field(ge=1)
    text: str = ""


class ParsedTable(SchemaModel):
    title: str | None = Field(default=None, max_length=1000)
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    extraction: Literal["extracted", "best_effort"] = "best_effort"


class ParsedDocument(SchemaModel):
    status: ParseStatus
    title: str | None = Field(default=None, max_length=5000)
    text: str = Field(min_length=1)
    sections: list[ParsedSection] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    paragraph_count: int = Field(default=0, ge=0)
    page_count: int = Field(default=0, ge=0)
    character_count: int = Field(ge=1)
    tables_status: Literal["extracted", "best_effort", "not_available"] = (
        "not_available"
    )
    parser_name: str = Field(min_length=1, max_length=64)
    parser_version: str = Field(min_length=1, max_length=64)
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _counts_match(self) -> "ParsedDocument":
        if self.character_count != len(self.text):
            raise ValueError("character_count must match the extracted text length")
        return self


class LLMResponse(SchemaModel):
    text: str
    model: str = Field(min_length=1, max_length=255)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    finish_reason: str | None = Field(default=None, max_length=64)


class SearchBundle(SchemaModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    failures: list[ProviderFailure] = Field(default_factory=list)


class AcademicBundle(SchemaModel):
    query: str
    results: list[AcademicResult] = Field(default_factory=list)
    failures: list[ProviderFailure] = Field(default_factory=list)
