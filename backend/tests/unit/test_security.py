"""Unit tests for the cryptographic primitives. No database required."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Environment, Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

SECRET = "unit-test-secret-key-long-enough-for-validation-0123"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ENVIRONMENT=Environment.TESTING,
        SECRET_KEY=SECRET,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
    )


class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert digest != "correct horse battery staple"
        assert digest.startswith("$argon2")

    def test_verify_accepts_correct_password(self) -> None:
        assert verify_password("s3cret-passphrase", hash_password("s3cret-passphrase"))

    def test_verify_rejects_wrong_password(self) -> None:
        assert not verify_password("wrong-passphrase", hash_password("s3cret-passphrase"))

    def test_salted_so_identical_passwords_differ(self) -> None:
        assert hash_password("same-password-1") != hash_password("same-password-1")

    def test_verify_rejects_malformed_hash_without_raising(self) -> None:
        assert not verify_password("anything", "not-a-valid-argon2-hash")


class TestAccessTokens:
    def test_round_trip(self, settings: Settings) -> None:
        user_id = uuid.uuid4()
        token, expires_at = create_access_token(settings, subject=user_id, role="USER")

        payload = decode_access_token(settings, token)

        assert payload["sub"] == str(user_id)
        assert payload["role"] == "USER"
        assert payload["type"] == "access"
        assert expires_at > datetime.now(UTC)

    def test_expired_token_is_rejected(self, settings: Settings) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        token, _ = create_access_token(settings, subject=uuid.uuid4(), role="USER", now=past)

        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(settings, token)

    def test_token_signed_with_another_key_is_rejected(self, settings: Settings) -> None:
        other = Settings(
            ENVIRONMENT=Environment.TESTING,
            SECRET_KEY="a-completely-different-secret-key-0123456789ab",
            DATABASE_URL=settings.DATABASE_URL,
        )
        token, _ = create_access_token(other, subject=uuid.uuid4(), role="USER")

        with pytest.raises(jwt.InvalidSignatureError):
            decode_access_token(settings, token)

    def test_alg_none_token_is_rejected(self, settings: Settings) -> None:
        """The classic JWT bypass: an unsigned token claiming `alg: none`."""
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "role": "ADMIN",
                "type": "access",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(jwt.PyJWTError):
            decode_access_token(settings, forged)

    def test_non_access_token_type_is_rejected(self, settings: Settings) -> None:
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "type": "refresh",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            },
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(jwt.InvalidTokenError):
            decode_access_token(settings, token)


class TestRefreshTokens:
    def test_tokens_are_unique_and_long(self) -> None:
        tokens = {generate_refresh_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(token) >= 32 for token in tokens)

    def test_hash_is_deterministic_and_irreversible(self) -> None:
        token = generate_refresh_token()
        assert hash_refresh_token(token) == hash_refresh_token(token)
        assert token not in hash_refresh_token(token)
        assert len(hash_refresh_token(token)) == 64
