"""Chat turn orchestration and streaming.

**Session lifecycle note.** This service opens its own database sessions instead of
using the request-scoped one. A streaming response's body is produced *after* the
endpoint returns, and a `yield`-based dependency's teardown timing relative to that
is a trap: writes made mid-stream can land in a session that is already closing.
Each write here is its own short, explicitly committed unit of work.

Three units of work per turn:

1. Authorise the conversation and persist the user message — committed before any
   model call, so a provider failure cannot lose what the user typed.
2. Stream from the provider.
3. Persist the assistant message and the usage record.

Step 3 runs on both the success and failure paths: a failed call still costs
latency and sometimes tokens, and hiding failures would make the reliability
numbers in later phases meaningless.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import Settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.session import Database
from app.llm.base import Completion, LLMProvider, Message, Role, StreamFinished, TextDelta
from app.llm.errors import LLMProviderError
from app.llm.pricing import estimate_cost_usd
from app.models.conversation import MessageRole
from app.models.llm_call import LLMCall, LLMCallStatus
from app.prompts.registry import get_prompt
from app.repositories.conversation import ConversationRepository
from app.repositories.llm_call import LLMCallRepository

logger = get_logger(__name__)

MAX_MESSAGE_LENGTH = 8_000
CHAT_SYSTEM_PROMPT = "chat_system"


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: dict[str, object]

    def encode(self) -> str:
        # Server-sent events are newline-delimited; JSON is compacted so a payload
        # can never contain a bare newline and split the frame.
        return f"event: {self.event}\ndata: {json.dumps(self.data, separators=(',', ':'))}\n\n"


class ChatService:
    def __init__(self, database: Database, settings: Settings, provider: LLMProvider) -> None:
        self._database = database
        self._settings = settings
        self._provider = provider

    async def stream_turn(
        self, *, conversation_id: uuid.UUID, user_id: uuid.UUID, content: str
    ) -> AsyncIterator[SSEEvent]:
        content = content.strip()
        if not content:
            raise ValidationError("Message content must not be empty.")
        if len(content) > MAX_MESSAGE_LENGTH:
            raise ValidationError(f"Message exceeds the {MAX_MESSAGE_LENGTH} character limit.")

        history, user_message_id = await self._persist_user_turn(
            conversation_id=conversation_id, user_id=user_id, content=content
        )

        yield SSEEvent(
            event="user_message",
            data={"message_id": str(user_message_id), "conversation_id": str(conversation_id)},
        )

        prompt = get_prompt(CHAT_SYSTEM_PROMPT)
        system_message = Message(
            role=Role.SYSTEM,
            content=prompt.render(current_date=datetime.now(UTC).date().isoformat()),
        )
        messages = [system_message, *history]

        collected: list[str] = []
        try:
            async for event in self._provider.stream(
                messages,
                temperature=self._settings.LLM_TEMPERATURE,
                max_tokens=self._settings.LLM_MAX_TOKENS,
            ):
                match event:
                    case TextDelta(text=text):
                        collected.append(text)
                        yield SSEEvent(event="delta", data={"text": text})
                    case StreamFinished(completion=completion):
                        assistant_message_id, cost = await self._persist_assistant_turn(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            text="".join(collected) or completion.text,
                            completion=completion,
                            prompt_name=prompt.name,
                            prompt_version=prompt.version,
                            prompt_checksum=prompt.checksum,
                        )
                        yield SSEEvent(
                            event="done",
                            data={
                                "message_id": str(assistant_message_id),
                                "provider": completion.provider,
                                "model": completion.model,
                                "is_test_double": completion.provider == "fake",
                                "finish_reason": completion.finish_reason,
                                "input_tokens": completion.usage.input_tokens,
                                "output_tokens": completion.usage.output_tokens,
                                "latency_ms": completion.latency_ms,
                                "cost_usd": str(cost) if cost is not None else None,
                            },
                        )
        except LLMProviderError as exc:
            await self._record_failure(
                conversation_id=conversation_id,
                user_id=user_id,
                error=exc,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_checksum=prompt.checksum,
                partial_text="".join(collected),
            )
            logger.warning(
                "chat_turn_failed",
                conversation_id=str(conversation_id),
                error_code=exc.error_code,
                had_partial_output=bool(collected),
            )
            yield SSEEvent(
                event="error",
                data={
                    "code": exc.error_code,
                    "message": exc.message,
                    # The client keeps whatever arrived rather than discarding it.
                    "partial": bool(collected),
                },
            )

    # --- units of work ------------------------------------------------------

    async def _persist_user_turn(
        self, *, conversation_id: uuid.UUID, user_id: uuid.UUID, content: str
    ) -> tuple[list[Message], uuid.UUID]:
        async with self._database.session() as session:
            repo = ConversationRepository(session)
            conversation = await repo.get_owned(conversation_id, user_id)
            if conversation is None:
                # Same answer for "does not exist" and "belongs to someone else".
                raise NotFoundError("Conversation not found.")

            stored = await repo.add_message(
                conversation_id=conversation_id, role=MessageRole.USER, content=content
            )

            if await repo.message_count(conversation_id) == 1:
                conversation.title = content[:80] + ("…" if len(content) > 80 else "")

            history_rows = await repo.list_messages(
                conversation_id, limit=self._settings.CHAT_HISTORY_LIMIT
            )
            history = [
                Message(
                    role=Role.USER if row.role is MessageRole.USER else Role.ASSISTANT,
                    content=row.content,
                )
                for row in history_rows
                if row.role is not MessageRole.SYSTEM
            ]
            return history, stored.id

    async def _persist_assistant_turn(
        self,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        text: str,
        completion: Completion,
        prompt_name: str,
        prompt_version: str,
        prompt_checksum: str,
    ) -> tuple[uuid.UUID, Decimal | None]:
        cost = estimate_cost_usd(completion.provider, completion.model, completion.usage)

        async with self._database.session() as session:
            repo = ConversationRepository(session)
            message = await repo.add_message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=text,
            )
            LLMCallRepository(session).record(
                LLMCall(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=message.id,
                    provider=completion.provider,
                    model=completion.model,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                    prompt_checksum=prompt_checksum,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cached_input_tokens=completion.usage.cached_input_tokens,
                    cost_usd=cost,
                    latency_ms=completion.latency_ms,
                    retries=completion.retries,
                    streamed=True,
                    status=LLMCallStatus.SUCCEEDED,
                    finish_reason=completion.finish_reason,
                )
            )
            return message.id, cost

    async def _record_failure(
        self,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        error: LLMProviderError,
        prompt_name: str,
        prompt_version: str,
        prompt_checksum: str,
        partial_text: str,
    ) -> None:
        async with self._database.session() as session:
            if partial_text:
                # A partial answer is still an answer; dropping it would lose work
                # the user watched arrive.
                await ConversationRepository(session).add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=partial_text,
                )
            LLMCallRepository(session).record(
                LLMCall(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    provider=self._provider.name,
                    model=self._provider.default_model,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                    prompt_checksum=prompt_checksum,
                    streamed=True,
                    status=LLMCallStatus.FAILED,
                    error_code=error.error_code,
                )
            )
