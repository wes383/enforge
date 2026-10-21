import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import approvals, files, health, projects, runs
from app.api.dev import router as dev_router
from dr_core.config import get_settings
from dr_core.db.repositories import DuplicateEntity, EntityNotFound
from dr_core.services import InvalidRunTransition, RunAlreadyFinished
from dr_core.storage import StorageError, get_file_storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_file_storage()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(health.root_router)
    application.include_router(health.v1_router, prefix=settings.api_v1_prefix)
    application.include_router(projects.router, prefix=settings.api_v1_prefix)
    application.include_router(runs.router, prefix=settings.api_v1_prefix)
    application.include_router(approvals.router, prefix=settings.api_v1_prefix)
    application.include_router(files.router, prefix=settings.api_v1_prefix)
    if settings.enable_dev_endpoints:
        application.include_router(dev_router, prefix=settings.api_v1_prefix)

    @application.exception_handler(EntityNotFound)
    async def entity_not_found_handler(
        _request: Request,
        exc: EntityNotFound,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(DuplicateEntity)
    async def duplicate_entity_handler(
        _request: Request,
        exc: DuplicateEntity,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(InvalidRunTransition)
    async def invalid_transition_handler(
        _request: Request,
        exc: InvalidRunTransition,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(RunAlreadyFinished)
    async def run_already_finished_handler(
        _request: Request,
        exc: RunAlreadyFinished,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(StorageError)
    async def storage_error_handler(
        _request: Request,
        exc: StorageError,
    ) -> JSONResponse:
        logger.error("File storage operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"detail": "File storage operation failed"},
        )

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    return application


app = create_app()
