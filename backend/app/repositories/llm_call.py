from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.models.llm_call import LLMCall, LLMCallStatus
from app.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class UsageSummary:
    calls: int
    failed_calls: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: Decimal
    #: Calls whose model had no entry in the pricing table. Reported rather than
    #: hidden, so `total_cost_usd` is never mistaken for complete.
    unpriced_calls: int
    average_latency_ms: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_coverage(self) -> float:
        """Share of calls that had a known price."""
        return 1.0 if self.calls == 0 else (self.calls - self.unpriced_calls) / self.calls


class LLMCallRepository(BaseRepository[LLMCall]):
    model = LLMCall

    def record(self, call: LLMCall) -> LLMCall:
        self.session.add(call)
        return call

    async def summarise(
        self, *, user_id: uuid.UUID | None = None, since: datetime | None = None
    ) -> UsageSummary:
        window_start = since or (datetime.now(UTC) - timedelta(days=1))

        stmt = select(
            func.count(LLMCall.id),
            func.count(LLMCall.id).filter(LLMCall.status == LLMCallStatus.FAILED),
            func.coalesce(func.sum(LLMCall.input_tokens), 0),
            func.coalesce(func.sum(LLMCall.output_tokens), 0),
            func.coalesce(func.sum(LLMCall.cost_usd), Decimal(0)),
            func.count(LLMCall.id).filter(LLMCall.cost_usd.is_(None)),
            func.coalesce(func.avg(LLMCall.latency_ms), 0.0),
        ).where(LLMCall.created_at >= window_start)

        if user_id is not None:
            stmt = stmt.where(LLMCall.user_id == user_id)

        row = (await self.session.execute(stmt)).one()
        return UsageSummary(
            calls=int(row[0]),
            failed_calls=int(row[1]),
            input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            total_cost_usd=Decimal(row[4]),
            unpriced_calls=int(row[5]),
            average_latency_ms=round(float(row[6]), 2),
        )
