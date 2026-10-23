from typing import Any
from uuid import UUID

from pydantic import Field

from dr_core.db.enums import RunEventType, RunStatus, SearchProviderName, SectionRole
from dr_core.schemas.base import SchemaModel


class OutlineSectionCreate(SchemaModel):
    id: UUID | None = None
    title: str = Field(min_length=1, max_length=1000)
    role: SectionRole = SectionRole.BODY
    purpose: str = Field(default="", max_length=5000)
    search_terms: list[str] = Field(default_factory=list, max_length=20)
    target_words: int | None = Field(default=None, ge=50, le=5000)
    priority: int = Field(default=0, ge=0, le=1000)


class SearchQueryCreate(SchemaModel):
    query_text: str = Field(min_length=1, max_length=2000)
    provider: SearchProviderName
    round_index: int = Field(default=1, ge=1, le=50)
    outline_section_id: UUID | None = None


class RunEventCreate(SchemaModel):
    event_type: RunEventType
    stage: RunStatus | None = None
    message: str | None = Field(default=None, max_length=20000)
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = Field(default=None, max_length=128)
