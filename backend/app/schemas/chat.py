from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.conversation import MessageRole
from app.services.chat import MAX_MESSAGE_LENGTH


class ConversationCreate(BaseModel):
    title: str = Field(default="New research", min_length=1, max_length=300)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be blank")
        return value


class UsageSummaryRead(BaseModel):
    """Aggregate spend and latency. Admin-only."""

    window_hours: int
    calls: int
    failed_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    total_cost_usd: Decimal
    #: Fraction of calls whose model had a known price. Below 1.0 means
    #: `total_cost_usd` understates the true figure.
    cost_coverage: float
    unpriced_calls: int
    average_latency_ms: float


class ProviderInfo(BaseModel):
    """What the API is actually talking to, so the UI need not guess."""

    provider: str
    model: str
    #: True when responses come from the deterministic test double.
    is_test_double: bool
