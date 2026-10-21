import asyncio
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psycopg
import redis.asyncio as redis
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from dr_core.config import get_settings
from dr_core.db.session import sqlalchemy_url
from dr_core.storage import get_file_storage

logger = logging.getLogger(__name__)

root_router = APIRouter(tags=["health"])
v1_router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "api"
    version: str
    environment: str
    timestamp: datetime


class ReadinessCheck(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


def _health_payload(settings: Any) -> HealthResponse:
    return HealthResponse(
        version=settings.app_version,
        environment=settings.environment,
        timestamp=datetime.now(tz=UTC),
    )


def _ping_database(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=3, autocommit=True) as connection:
        connection.execute("SELECT 1").fetchone()


async def _ping_redis(redis_url: str) -> None:
    client = redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        decode_responses=True,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


def _probe_storage() -> None:
    storage = get_file_storage()
    key = f"_health/{uuid4().hex}.probe"
    storage.save(key, b"ok")
    try:
        if storage.read(key) != b"ok":
            raise RuntimeError("Storage probe returned unexpected content")
    finally:
        storage.delete(key)


@lru_cache(maxsize=1)
def _expected_schema_revision() -> str | None:
    config_path = get_settings().alembic_config_path
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    if not config_path.is_file():
        raise RuntimeError("Alembic configuration file is missing")
    config = Config(str(config_path))
    return ScriptDirectory.from_config(config).get_current_head()


def _check_schema(database_url: str) -> None:
    expected = _expected_schema_revision()
    engine = create_engine(sqlalchemy_url(database_url), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    if current != expected:
        raise RuntimeError(
            f"Database schema revision {current!r} does not match head {expected!r}"
        )


async def _run_check(name: str, check: Any) -> ReadinessCheck:
    started = time.perf_counter()
    try:
        result = check() if callable(check) else check
        await asyncio.wait_for(result, timeout=4.0)
    except Exception as exc:
        logger.warning("Readiness check failed: %s", name, exc_info=True)
        return ReadinessCheck(
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            error=type(exc).__name__,
        )
    return ReadinessCheck(
        status="ok",
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@root_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return _health_payload(get_settings())


@root_router.get("/ready")
async def ready() -> JSONResponse:
    settings = get_settings()
    checks = await asyncio.gather(
        _run_check(
            "database",
            run_in_threadpool(_ping_database, settings.database_url),
        ),
        _run_check(
            "schema",
            run_in_threadpool(_check_schema, settings.database_url),
        ),
        _run_check("redis", _ping_redis(settings.redis_url)),
        _run_check("storage", run_in_threadpool(_probe_storage)),
    )
    names = ("database", "schema", "redis", "storage")
    payload = {name: check.model_dump() for name, check in zip(names, checks, strict=True)}
    is_ready = all(check.status == "ok" for check in checks)
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": payload},
    )


@v1_router.get("/health", response_model=HealthResponse)
async def v1_health() -> HealthResponse:
    return _health_payload(get_settings())
