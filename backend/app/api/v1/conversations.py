"""Conversation CRUD and the streaming chat turn."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import AppSettings, CurrentUser, DbSession, get_database, get_llm_provider
from app.core.exceptions import InsightAgentError, NotFoundError
from app.core.logging import get_logger
from app.db.session import Database
from app.llm.base import LLMProvider
from app.repositories.conversation import ConversationRepository
from app.schemas.chat import (
    ChatRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
    ProviderInfo,
)
from app.services.chat import ChatService, SSEEvent

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, session: DbSession, user: CurrentUser
) -> ConversationRead:
    conversation = await ConversationRepository(session).create(
        user_id=user.id, title=payload.title
    )
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationRead]:
    conversations = await ConversationRepository(session).list_for_user(
        user.id, limit=limit, offset=offset
    )
    return [ConversationRead.model_validate(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> ConversationDetail:
    repo = ConversationRepository(session)
    conversation = await repo.get_owned(conversation_id, user.id)
    if conversation is None:
        raise NotFoundError("Conversation not found.")

    messages = await repo.list_messages(conversation_id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageRead.model_validate(message) for message in messages],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> None:
    repo = ConversationRepository(session)
    conversation = await repo.get_owned(conversation_id, user.id)
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    await repo.delete(conversation)


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    payload: ChatRequest,
    request: Request,
    user: CurrentUser,
    settings: AppSettings,
    database: Annotated[Database, Depends(get_database)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> StreamingResponse:
    """Stream the assistant's reply as server-sent events.

    Events: `user_message`, then `delta` repeatedly, then exactly one of `done` or
    `error`. The service persists both messages and the usage record; this
    endpoint only frames the stream.
    """
    service = ChatService(database, settings, provider)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in service.stream_turn(
                conversation_id=conversation_id, user_id=user.id, content=payload.content
            ):
                yield event.encode()
                if await request.is_disconnected():
                    # The client navigated away. Stop generating; the turn already
                    # persisted whatever completed.
                    logger.info(
                        "chat_stream_client_disconnected", conversation_id=str(conversation_id)
                    )
                    return
        except InsightAgentError as exc:
            # Raised before streaming begins (unknown conversation, invalid input).
            # The response status is already 200, so the failure has to travel as
            # an event rather than an HTTP error.
            yield SSEEvent(
                event="error", data={"code": exc.error_code, "message": exc.message}
            ).encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # stops nginx buffering the stream
            "Connection": "keep-alive",
        },
    )


provider_router = APIRouter(prefix="/llm", tags=["llm"])


@provider_router.get("/provider", response_model=ProviderInfo)
async def current_provider(
    user: CurrentUser,
    settings: AppSettings,
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> ProviderInfo:
    return ProviderInfo(
        provider=provider.name,
        model=provider.default_model,
        is_test_double=provider.name == "fake",
    )
