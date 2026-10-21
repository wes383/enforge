from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Deep Research Writing Agent"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql://enforge:enforge_dev_password@localhost:5432/enforge"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_default_queue: str = "research"
    celery_parse_queue: str = "parse"

    run_task_max_retries: int = Field(default=3, ge=0, le=10)
    run_task_soft_time_limit_seconds: int = Field(default=300, ge=1)
    run_task_hard_time_limit_seconds: int = Field(default=360, ge=1)

    sse_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    sse_keepalive_seconds: float = Field(default=15.0, ge=1.0)

    file_storage_root: Path = Path("data/files")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)

    provider_request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    provider_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    provider_max_retries: int = Field(default=3, ge=0, le=10)
    provider_retry_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    provider_cache_ttl_seconds: int = Field(default=900, ge=0)
    provider_cache_max_entries: int = Field(default=512, ge=1)

    tavily_enabled: bool = False
    tavily_api_key: SecretStr | None = None
    exa_enabled: bool = False
    exa_api_key: SecretStr | None = None
    firecrawl_enabled: bool = False
    firecrawl_api_key: SecretStr | None = None
    firecrawl_max_concurrency: int = Field(default=2, ge=1, le=8)
    crossref_enabled: bool = False
    crossref_mailto: str | None = None
    semantic_scholar_enabled: bool = False
    semantic_scholar_api_key: SecretStr | None = None

    web_fetch_max_bytes: int = Field(default=2_000_000, gt=0)
    web_fetch_min_content_chars: int = Field(default=400, ge=0)

    parser_max_input_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    parser_max_output_chars: int = Field(default=2_000_000, gt=0)
    parser_max_pdf_pages: int = Field(default=200, ge=1, le=2000)
    parse_task_soft_time_limit_seconds: int = Field(default=240, ge=1)
    parse_task_hard_time_limit_seconds: int = Field(default=300, ge=1)

    # Research workflow. "auto" uses real providers when enabled and falls back
    # to deterministic mocks outside production when none are configured.
    research_provider_mode: Literal["auto", "real", "mock"] = "auto"
    research_max_search_rounds: int = Field(default=3, ge=1, le=10)
    research_max_queries_per_round: int = Field(default=6, ge=1, le=30)
    research_max_sources: int = Field(default=20, ge=1, le=200)
    research_max_fetches_per_round: int = Field(default=8, ge=0, le=100)
    research_max_evidence: int = Field(default=40, ge=1, le=500)
    research_max_fetch_concurrency: int = Field(default=2, ge=1, le=8)
    research_node_timeout_seconds: int = Field(default=90, ge=1, le=1800)
    research_token_budget: int = Field(default=0, ge=0)
    research_cost_budget_usd: float = Field(default=0.0, ge=0)
    research_plan_approval_required: bool = True
    research_outline_approval_required: bool = True
    research_retrieval_mode: Literal["hybrid", "keyword", "vector"] = "hybrid"
    research_retrieval_limit: int = Field(default=10, ge=1, le=100)
    research_chunk_max_chars: int = Field(default=1200, ge=200, le=10000)

    # pgvector's HNSW index supports at most 2000 dimensions. Changing this
    # value requires a new Alembic migration for document_chunks.embedding.
    embedding_dimension: int = Field(default=1536, ge=1, le=2000)
    alembic_config_path: Path = Path("alembic.ini")

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    enable_dev_endpoints: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
