"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.cache.redis import create_redis
from app.core.config import Settings, get_settings
from app.core.exceptions import InsightAgentError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.session import create_database

logger = get_logger(__name__)


def _error_body(
    *, code: str, message: str, request_id: str | None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if request_id:
        body["error"]["request_id"] = request_id
    if details:
        body["error"]["details"] = details
    return body


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.database = create_database(settings)
    app.state.redis = create_redis(settings)
    logger.info(
        "application_started",
        environment=settings.ENVIRONMENT.value,
        version=settings.VERSION,
    )
    try:
        yield
    finally:
        await app.state.database.dispose()
        await app.state.redis.aclose()
        logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="Autonomous AI research and data analysis agent.",
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url=f"{settings.API_V1_PREFIX}/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.exception_handler(InsightAgentError)
    async def handle_application_error(request: Request, exc: InsightAgentError) -> JSONResponse:
        logger.warning(
            "application_error",
            error_code=exc.error_code,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=exc.error_code,
                message=exc.message,
                request_id=_request_id(request),
                details=exc.details or None,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field-level errors are safe to return: they describe the client's own
        # payload. `input` is stripped so a mistyped password is never echoed back.
        details = [
            {"field": ".".join(str(part) for part in err["loc"][1:]), "reason": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body(
                code="validation_error",
                message="The request payload is invalid.",
                request_id=_request_id(request),
                details={"fields": details},
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path)
        # Never surface the exception text in production: it routinely contains
        # table names, SQL fragments, or file paths (brief §32, §60).
        details = (
            {"exception": type(exc).__name__, "message": str(exc)}
            if not settings.is_production
            else None
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(
                code="internal_error",
                message="An internal error occurred.",
                request_id=_request_id(request),
                details=details,
            ),
        )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()
