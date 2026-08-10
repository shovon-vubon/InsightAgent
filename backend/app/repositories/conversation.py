"""Conversation access.

Deliberately built on `OwnedRepository` in Phase 1, before any endpoint exists, so
that Phase 2's chat feature cannot accidentally introduce an unscoped read: the
only way to build a query here is to supply an owner id.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute

from app.models.conversation import Conversation, Message, MessageRole
from app.repositories.base import OwnedRepository


class ConversationRepository(OwnedRepository[Conversation]):
    model = Conversation

    @classmethod
    def owner_column(cls) -> InstrumentedAttribute[Any]:
        return Conversation.user_id

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        stmt = (
            self.scoped(user_id)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, *, user_id: uuid.UUID, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def delete(self, conversation: Conversation) -> None:
        await self.session.delete(conversation)
        # Flush so the row is gone from this session's view immediately. The
        # session runs with autoflush off, so without this a query later in the
        # same unit of work would still return the "deleted" row.
        await self.session.flush()

    async def next_sequence(self, conversation_id: uuid.UUID) -> int:
        """Next message ordinal. Relies on the unique index to settle races."""
        result = await self.session.execute(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one()) + 1

    async def add_message(
        self, *, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            sequence=await self.next_sequence(conversation_id),
            role=role,
            content=content,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_messages(
        self, conversation_id: uuid.UUID, *, limit: int | None = None
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        if limit is not None:
            # Take the newest `limit` rows, then restore chronological order —
            # the model needs the tail of the conversation, not its head.
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence.desc())
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            return list(reversed(result.scalars().all()))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def message_count(self, conversation_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        return int(result.scalar_one())
