"""Liveness and readiness probes.

Split deliberately: an orchestrator restarts a container that fails *liveness*, but
only removes it from the load balancer when it fails *readiness*. Probing the
database in the liveness check would turn a brief database blip into a restart loop.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.deps import AppSettings, get_database, get_redis
from app.api.route import CommittingRoute
from app.cache.redis import Cache
from app.db.session import Database

router = APIRouter(prefix="/health", tags=["health"], route_class=CommittingRoute)

ComponentStatus = Literal["ok", "unavailable"]


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    database: ComponentStatus
    redis: ComponentStatus


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live(settings: AppSettings) -> LivenessResponse:
    return LivenessResponse(version=settings.VERSION, environment=settings.ENVIRONMENT.value)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(
    response: Response,
    database: Annotated[Database, Depends(get_database)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ReadinessResponse:
    database_status: ComponentStatus = "unavailable"
    try:
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
        database_status = "ok"
    except Exception:
        database_status = "unavailable"

    redis_status: ComponentStatus = "ok" if await Cache(redis).ping() else "unavailable"

    ready_now = database_status == "ok" and redis_status == "ok"
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready_now else "degraded",
        database=database_status,
        redis=redis_status,
    )
