from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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

    # Paces orchestration phases so cancellation and SSE can be observed.
    # Set to 0 for production workloads.
    run_phase_pause_seconds: float = Field(default=0.35, ge=0.0, le=10.0)
    run_task_max_retries: int = Field(default=3, ge=0, le=10)
    run_task_soft_time_limit_seconds: int = Field(default=300, ge=1)
    run_task_hard_time_limit_seconds: int = Field(default=360, ge=1)

    sse_poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    sse_keepalive_seconds: float = Field(default=15.0, ge=1.0)

    file_storage_root: Path = Path("data/files")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)

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
