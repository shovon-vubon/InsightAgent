"""Per-call LLM usage, latency, and cost (brief §26, §28).

One row per request to a provider, whether it succeeded or not — failures are the
interesting ones for reliability work, so they are recorded rather than dropped.

`cost_usd` is nullable on purpose: a model with no entry in the pricing table
records NULL rather than a guessed number, and aggregate reporting states its
price coverage.

`agent_run_id` arrives with `agent_runs` in Phase 7; until then a call is
attributed to its conversation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class LLMCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


LLMCallStatusType = Enum(
    LLMCallStatus,
    native_enum=False,
    length=16,
    validate_strings=True,
    name="llm_call_status",
)


class LLMCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_created_at", "created_at"),
        Index("ix_llm_calls_user_id_created_at", "user_id", "created_at"),
        Index("ix_llm_calls_provider_model", "provider", "model"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), default=None
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), default=None
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    # Which prompt text produced this call, so results stay attributable.
    prompt_name: Mapped[str | None] = mapped_column(String(64), default=None)
    prompt_version: Mapped[str | None] = mapped_column(String(16), default=None)
    prompt_checksum: Mapped[str | None] = mapped_column(String(32), default=None)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)

    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    streamed: Mapped[bool] = mapped_column(default=False, nullable=False)

    status: Mapped[LLMCallStatus] = mapped_column(LLMCallStatusType, nullable=False)
    finish_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
