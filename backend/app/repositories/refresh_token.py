from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update

from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        expires_at: datetime,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def revoke(self, token: RefreshToken, *, replaced_by_id: uuid.UUID | None = None) -> None:
        token.revoked_at = datetime.now(UTC)
        token.replaced_by_id = replaced_by_id
        await self.session.flush()

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke every unrevoked token in a rotation family. Returns the count.

        Called on replay detection: one leaked token invalidates the whole chain,
        forcing re-authentication.
        """
        # `execute()` is typed as returning Result; an UPDATE always yields a
        # CursorResult, which is where rowcount lives.
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        await self.session.flush()
        return result.rowcount or 0

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        await self.session.flush()
        return result.rowcount or 0
