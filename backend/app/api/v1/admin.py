"""Admin-only operational views.

Phase 2 exposes aggregate LLM spend. Phase 10 adds the full run trace viewer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession
from app.api.route import CommittingRoute
from app.repositories.llm_call import LLMCallRepository
from app.schemas.chat import UsageSummaryRead

router = APIRouter(prefix="/admin", tags=["admin"], route_class=CommittingRoute)


@router.get("/usage", response_model=UsageSummaryRead)
async def usage_summary(
    session: DbSession,
    _admin: AdminUser,
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> UsageSummaryRead:
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    summary = await LLMCallRepository(session).summarise(since=since)

    return UsageSummaryRead(
        window_hours=window_hours,
        calls=summary.calls,
        failed_calls=summary.failed_calls,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        total_tokens=summary.total_tokens,
        total_cost_usd=summary.total_cost_usd,
        cost_coverage=round(summary.cost_coverage, 4),
        unpriced_calls=summary.unpriced_calls,
        average_latency_ms=summary.average_latency_ms,
    )
