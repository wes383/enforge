from datetime import date
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from dr_core.db.enums import (
    SourceOrigin,
    SourceType,
    SourceVersionKind,
)
from dr_core.normalization import normalize_doi, normalize_url
from dr_core.schemas.base import SchemaModel
from dr_core.storage.keys import validate_metadata_storage_key

AuthorName = Annotated[str, StringConstraints(min_length=1, max_length=300)]
ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SourceCreate(SchemaModel):
    title: str = Field(min_length=1, max_length=5000)
    authors: list[AuthorName] = Field(default_factory=list, max_length=200)
    published_at: date | None = None
    doi: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=4096)
    source_type: SourceType = SourceType.WEB_PAGE
    source_origin: SourceOrigin = SourceOrigin.MANUAL
    language: str | None = Field(default=None, max_length=16)
    venue: str | None = Field(default=None, max_length=1000)
    publisher: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doi")
    @classmethod
    def _normalize_doi(cls, value: str | None) -> str | None:
        return normalize_doi(value) if value else None

    @field_validator("url")
    @classmethod
    def _normalize_url(cls, value: str | None) -> str | None:
        return normalize_url(value) if value else None


class SourceVersionCreate(SchemaModel):
    content_kind: SourceVersionKind
    content_hash: ContentHash
    storage_key: str | None = Field(default=None, max_length=512)
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    fetched_url: str | None = Field(default=None, max_length=4096)
    license: str | None = Field(default=None, max_length=2000)
    parser_version: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str | None) -> str | None:
        return validate_metadata_storage_key(value) if value else None

    @field_validator("fetched_url")
    @classmethod
    def _normalize_fetched_url(cls, value: str | None) -> str | None:
        return normalize_url(value) if value else None

    @model_validator(mode="after")
    def _require_storage_for_materialized_content(self) -> "SourceVersionCreate":
        if (
            self.content_kind
            in {SourceVersionKind.FULL_TEXT, SourceVersionKind.UPLOADED_DOCUMENT}
            and not self.storage_key
        ):
            raise ValueError(
                "storage_key is required for full text and uploaded documents"
            )
        return self


class DocumentChunkCreate(SchemaModel):
    chunk_index: int = Field(ge=0)
    section_title: str | None = Field(default=None, max_length=2000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    paragraph_start: int | None = Field(default=None, ge=0)
    paragraph_end: int | None = Field(default=None, ge=0)
    text: str = Field(min_length=1)
    token_count: int | None = Field(default=None, ge=0)
    embedding: list[float] | None = None
    embedding_model: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_ranges(self) -> "DocumentChunkCreate":
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end must be greater than or equal to page_start")
        if (
            self.paragraph_start is not None
            and self.paragraph_end is not None
            and self.paragraph_end < self.paragraph_start
        ):
            raise ValueError(
                "paragraph_end must be greater than or equal to paragraph_start"
            )
        if self.embedding is not None and not self.embedding_model:
            raise ValueError("embedding_model is required when embedding is provided")
        return self


class SourceVersionReference(SchemaModel):
    source_id: UUID
    source_version_id: UUID
