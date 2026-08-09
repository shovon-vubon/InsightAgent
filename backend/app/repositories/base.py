"""Repository layer: data access only, no business rules.

`OwnedRepository` exists to make the IDOR guard structural. Any table with a
`user_id` gets its repository from `OwnedRepository`, which cannot produce an
unscoped query — the caller has to pass an owner id to get a statement at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.base import Base


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        return entity

    async def flush(self) -> None:
        await self.session.flush()


class OwnedRepository[ModelT: Base](BaseRepository[ModelT]):
    """Base for user-owned resources. Subclasses declare the ownership column.

    `owner_column` is a classmethod rather than a class attribute on purpose: a
    mapped column assigned as a plain class attribute is an `InstrumentedAttribute`,
    which is a descriptor. Reading it through `self` invokes `__get__` with the
    repository as the instance and raises `UnmappedInstanceError`.
    """

    @classmethod
    def owner_column(cls) -> InstrumentedAttribute[Any]:
        raise NotImplementedError

    def scoped(self, user_id: uuid.UUID) -> Select[tuple[ModelT]]:
        """The only entry point for building a query over this table."""
        return select(self.model).where(self.owner_column() == user_id)

    async def get_owned(self, entity_id: uuid.UUID, user_id: uuid.UUID) -> ModelT | None:
        """Fetch by id *and* owner.

        Returning ``None`` for a row owned by someone else — rather than raising a
        distinct error — keeps existence unobservable to a probing client.
        """
        stmt = self.scoped(user_id).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
