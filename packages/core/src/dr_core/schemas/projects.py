from decimal import Decimal

from pydantic import Field

from dr_core.db.enums import CitationStyle, ProjectLanguage, ResearchDepth
from dr_core.schemas.base import SchemaModel


class ProjectCreate(SchemaModel):
    title: str = Field(min_length=1, max_length=300)
    topic: str = Field(min_length=1, max_length=20000)
    research_scope: str | None = Field(default=None, max_length=20000)
    language: ProjectLanguage = ProjectLanguage.ZH
    target_audience: str | None = Field(default=None, max_length=1000)
    research_depth: ResearchDepth = ResearchDepth.STANDARD
    citation_style: CitationStyle = CitationStyle.APA
    budget_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=4,
    )


class ProjectUpdate(SchemaModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    topic: str | None = Field(default=None, min_length=1, max_length=20000)
    research_scope: str | None = Field(default=None, max_length=20000)
    language: ProjectLanguage | None = None
    target_audience: str | None = Field(default=None, max_length=1000)
    research_depth: ResearchDepth | None = None
    citation_style: CitationStyle | None = None
    budget_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=4,
    )


class RunCreate(SchemaModel):
    idempotency_key: str | None = Field(default=None, max_length=128)
