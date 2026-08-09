"""Authentication business logic.

Token model:

* **Access** — 15-minute JWT, returned in the response body, held in memory by the
  client. Short TTL is what limits the damage of a leak, since it cannot be revoked.
* **Refresh** — 30-day opaque token in an HttpOnly cookie, rotated on every use and
  chained into a family. Replaying a spent token revokes the entire family.
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.user import User, UserRole
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    """A real argon2 hash to verify against when the account does not exist.

    Without this, a missing account returns measurably faster than a wrong
    password, which turns the login endpoint into a user-enumeration oracle.
    """
    return hash_password(uuid.uuid4().hex + uuid.uuid4().hex)


@dataclass(slots=True, frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    # --- registration -------------------------------------------------------

    async def register(self, *, email: str, password: str, full_name: str | None = None) -> User:
        if len(password) < self.settings.PASSWORD_MIN_LENGTH:
            raise ConflictError(
                f"Password must be at least {self.settings.PASSWORD_MIN_LENGTH} characters."
            )
        if await self.users.get_by_email(email) is not None:
            # Accepted tradeoff: this confirms an address is registered. Avoiding
            # it requires an email-confirmation flow, which this project does not
            # have. Documented in docs/SECURITY.md.
            raise ConflictError("An account with that email already exists.")

        try:
            user = await self.users.create(
                email=email, password_hash=hash_password(password), full_name=full_name
            )
        except IntegrityError as exc:  # concurrent registration of the same email
            raise ConflictError("An account with that email already exists.") from exc

        logger.info("user_registered", user_id=str(user.id))
        return user

    # --- login --------------------------------------------------------------

    async def authenticate(self, *, email: str, password: str) -> User:
        user = await self.users.get_by_email(email)
        if user is None:
            verify_password(password, _dummy_password_hash())
            logger.info("login_failed", reason="unknown_email")
            raise AuthenticationError()
        if not verify_password(password, user.password_hash):
            logger.info("login_failed", reason="bad_password", user_id=str(user.id))
            raise AuthenticationError()
        if not user.is_active:
            logger.warning("login_failed", reason="inactive_account", user_id=str(user.id))
            raise AuthenticationError()
        return user

    # --- token issuing ------------------------------------------------------

    async def issue_tokens(
        self,
        user: User,
        *,
        family_id: uuid.UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> IssuedTokens:
        access_token, access_expires_at = create_access_token(
            self.settings, subject=user.id, role=user.role.value
        )
        raw_refresh = generate_refresh_token()
        refresh_expires_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.REFRESH_TOKEN_TTL_SECONDS
        )
        await self.tokens.create(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            family_id=family_id or uuid.uuid4(),
            expires_at=refresh_expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return IssuedTokens(
            access_token=access_token,
            access_expires_at=access_expires_at,
            refresh_token=raw_refresh,
            refresh_expires_at=refresh_expires_at,
        )

    # --- rotation -----------------------------------------------------------

    async def rotate_refresh_token(
        self,
        raw_token: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> IssuedTokens:
        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))
        if stored is None:
            raise AuthenticationError("Invalid refresh token.")

        if stored.is_revoked:
            # Replay of a spent token: the value leaked. Kill the whole chain.
            revoked = await self.tokens.revoke_family(stored.family_id)
            # Commit before raising — the request-scoped unit of work rolls back on
            # exception, which would silently undo the revocation we just performed.
            await self.session.commit()
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(stored.user_id),
                family_id=str(stored.family_id),
                revoked_count=revoked,
            )
            raise AuthenticationError("Invalid refresh token.")

        if stored.expires_at <= datetime.now(UTC):
            raise AuthenticationError("Refresh token has expired.")

        user = await self.users.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token.")

        issued = await self.issue_tokens(
            user, family_id=stored.family_id, user_agent=user_agent, ip_address=ip_address
        )
        successor = await self.tokens.get_by_hash(hash_refresh_token(issued.refresh_token))
        await self.tokens.revoke(stored, replaced_by_id=successor.id if successor else None)
        return issued

    # --- logout -------------------------------------------------------------

    async def logout(self, raw_token: str | None) -> None:
        """Idempotent: an absent or unknown token is not an error."""
        if not raw_token:
            return
        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))
        if stored is None:
            return
        await self.tokens.revoke_family(stored.family_id)
        logger.info("user_logged_out", user_id=str(stored.user_id))

    # --- bootstrap ----------------------------------------------------------

    async def ensure_admin(self, *, email: str, password: str) -> User:
        """Create or promote the bootstrap admin. Used by `make seed`, not by the API."""
        user = await self.users.get_by_email(email)
        if user is None:
            user = await self.users.create(
                email=email, password_hash=hash_password(password), role=UserRole.ADMIN
            )
        elif user.role is not UserRole.ADMIN:
            user.role = UserRole.ADMIN
            await self.session.flush()
        return user
