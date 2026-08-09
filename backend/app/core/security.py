"""Password hashing, JWT issuing, and opaque-token primitives.

Two distinct token kinds, deliberately handled differently:

* **Access token** — a short-lived signed JWT. Stateless; never stored server-side.
* **Refresh token** — a long, opaque, high-entropy random string. Only its SHA-256
  digest is stored, so a database leak does not yield usable tokens. It is not a
  password, so a slow KDF buys nothing here; the value already has 256 bits of
  entropy and is not guessable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

# argon2id with the library defaults, which track the OWASP recommendations.
_password_hasher = PasswordHasher()

REFRESH_TOKEN_BYTES: Final = 32
TokenType = Literal["access"]


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def password_needs_rehash(password_hash: str) -> bool:
    """True when argon2 parameters have since been strengthened."""
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(
    settings: Settings, *, subject: uuid.UUID, role: str, now: datetime | None = None
) -> tuple[str, datetime]:
    """Return the signed JWT and its expiry."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": "access",
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )
    return token, expires_at


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    """Decode and verify an access token.

    Raises `jwt.PyJWTError` on any failure. The algorithm is pinned to the
    configured one so a token claiming `alg: none` — or a different HMAC size —
    cannot be substituted.
    """
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "iat", "sub", "type"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
