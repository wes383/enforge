from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Worst-case number of model calls the writing node can make in one run; used to
# derive the Celery time-limit floor when model writing is enabled.
LLM_SECTION_CALL_BUDGET = 12
# Planning asks the model once per plan version, so a run that is revised by the
# approver can make a couple of planning calls before searching starts.
LLM_PLANNING_CALL_BUDGET = 2


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

    # Optional OpenAI-compatible chat model for planning, section writing and
    # claim review. Registration only happens when a key is present; the workflow
    # still requires RESEARCH_LLM_PLANNING_ENABLED / RESEARCH_LLM_WRITING_ENABLED /
    # RESEARCH_CLAIM_LLM_REVIEW to call it.
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # Model generation is far slower than search APIs (reasoning models spend tens
    # of seconds per section), so it gets its own timeout and retry budget instead
    # of inheriting the provider defaults.
    llm_request_timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    llm_max_retries: int = Field(default=0, ge=0, le=5)

    # Optional per-million-token prices for the configured endpoint. Cost stays 0
    # when unset, but token usage is always recorded against the run.
    llm_cost_per_1m_input_tokens: float = Field(default=0.0, ge=0)
    llm_cost_per_1m_output_tokens: float = Field(default=0.0, ge=0)

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
    research_llm_planning_enabled: bool = False
    research_llm_writing_enabled: bool = False
    research_claim_llm_review: bool = False
    # Caps paid model calls per run so verification time stays bounded.
    research_claim_llm_review_limit: int = Field(default=10, ge=1, le=500)
    max_active_runs_per_project: int = Field(default=3, ge=0, le=100)

    # Optional Langfuse ingestion. Disabled by default so the MVP runs fully
    # offline; when enabled, trace metadata is sent without prompts, source
    # text, or credentials unless observability_include_content is explicitly set.
    langfuse_enabled: bool = False
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    observability_include_content: bool = False

    # pgvector's HNSW index supports at most 2000 dimensions. Changing this
    # value requires a new Alembic migration for document_chunks.embedding.
    embedding_dimension: int = Field(default=1536, ge=1, le=2000)
    alembic_config_path: Path = Path("alembic.ini")

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    enable_dev_endpoints: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_workflow_enabled(self) -> bool:
        return (
            self.research_llm_planning_enabled
            or self.research_llm_writing_enabled
            or self.research_claim_llm_review
        )

    @property
    def llm_call_budget_seconds(self) -> int:
        """Worst-case wall time of one model call, retries included."""
        return int((self.llm_max_retries + 1) * self.llm_request_timeout_seconds)

    def llm_usage_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        """Cost of one model call, rounded to the run column's 6 decimals."""
        input_rate = Decimal(str(self.llm_cost_per_1m_input_tokens))
        output_rate = Decimal(str(self.llm_cost_per_1m_output_tokens))
        if input_rate == 0 and output_rate == 0:
            return Decimal("0")
        cost = (
            input_rate * max(0, input_tokens)
            + output_rate * max(0, output_tokens)
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"))

    @property
    def llm_run_time_floor_seconds(self) -> int:
        """Ceiling a model-writing run needs before Celery may kill it."""
        calls = LLM_SECTION_CALL_BUDGET + self.research_claim_llm_review_limit
        if self.research_llm_planning_enabled:
            calls += LLM_PLANNING_CALL_BUDGET
        return self.llm_call_budget_seconds * calls + self.research_node_timeout_seconds

    @property
    def effective_run_task_soft_time_limit_seconds(self) -> int:
        if not self.llm_workflow_enabled:
            return self.run_task_soft_time_limit_seconds
        return max(
            self.run_task_soft_time_limit_seconds,
            self.llm_run_time_floor_seconds,
        )

    @property
    def effective_run_task_hard_time_limit_seconds(self) -> int:
        soft = self.effective_run_task_soft_time_limit_seconds
        return max(self.run_task_hard_time_limit_seconds, soft + 60)


@lru_cache
def get_settings() -> Settings:
    return Settings()
