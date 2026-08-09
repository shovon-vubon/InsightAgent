"""Request-scoped context and access logging.

Every log line emitted while handling a request carries the same `request_id`, and
the id is echoed in the response header and in error payloads so a user-reported
failure maps to exact log records.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Trust an inbound id only for correlation. It is never used for anything
        # security-relevant, and it is length-capped so it cannot bloat log records.
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = inbound[:64] if inbound else uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers own the response; this only records timing.
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        # Health probes fire constantly; logging them at INFO drowns everything else.
        level = "debug" if request.url.path.startswith("/api/v1/health") else "info"
        getattr(logger, level)(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response
