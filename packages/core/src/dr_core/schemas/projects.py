from datetime import date
from decimal import Decimal

from pydantic import Field, model_validator

from dr_core.db.enums import ArticleType, CitationStyle, ProjectLanguage, ResearchDepth
from dr_core.schemas.base import SchemaModel


class ProjectCreate(SchemaModel):
    title: str = Field(min_length=1, max_length=300)
    topic: str = Field(min_length=1, max_length=20000)
    research_scope: str | None = Field(default=None, max_length=20000)
    article_type: ArticleType = ArticleType.INVESTIGATIVE_RESEARCH
    language: ProjectLanguage = ProjectLanguage.ZH
    target_audience: str | None = Field(default=None, max_length=1000)
    research_depth: ResearchDepth = ResearchDepth.STANDARD
    citation_style: CitationStyle = CitationStyle.APA
    target_words: int | None = Field(default=None, ge=100, le=50000)
    date_from: date | None = None
    date_to: date | None = None
    budget_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=4,
    )

    @model_validator(mode="after")
    def _validate_date_range(self) -> "ProjectCreate":
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class ProjectUpdate(SchemaModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    topic: str | None = Field(default=None, min_length=1, max_length=20000)
    research_scope: str | None = Field(default=None, max_length=20000)
    article_type: ArticleType | None = None
    language: ProjectLanguage | None = None
    target_audience: str | None = Field(default=None, max_length=1000)
    research_depth: ResearchDepth | None = None
    citation_style: CitationStyle | None = None
    target_words: int | None = Field(default=None, ge=100, le=50000)
    date_from: date | None = None
    date_to: date | None = None
    budget_usd: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=4,
    )

    @model_validator(mode="after")
    def _validate_date_range(self) -> "ProjectUpdate":
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class RunCreate(SchemaModel):
    idempotency_key: str | None = Field(default=None, max_length=128)
