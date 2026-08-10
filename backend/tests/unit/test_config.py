"""The production guardrails in Settings are themselves worth testing.

Each of these represents a real deployment mistake the validator is there to stop.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import PLACEHOLDER_SECRET, Environment, Settings

VALID_SECRET = "a-perfectly-adequate-secret-key-0123456789abcdef"
VALID_DB_URL = "postgresql+asyncpg://user:pass@localhost:5432/db"


def build(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "ENVIRONMENT": Environment.PRODUCTION,
        "DEBUG": False,
        "SECRET_KEY": VALID_SECRET,
        "DATABASE_URL": VALID_DB_URL,
        "LOG_FORMAT": "json",
        "CORS_ORIGINS": "https://insightagent.example",
        "LLM_PROVIDER": "openai",
        "EMBEDDING_PROVIDER": "ollama",
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def test_valid_production_settings_construct() -> None:
    settings = build()
    assert settings.is_production
    assert settings.cors_origins == ["https://insightagent.example"]


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        build(SECRET_KEY="too-short")


def test_placeholder_secret_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        build(SECRET_KEY=PLACEHOLDER_SECRET)


def test_debug_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        build(DEBUG=True)


def test_wildcard_cors_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        build(CORS_ORIGINS="*")


def test_console_logging_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="LOG_FORMAT"):
        build(LOG_FORMAT="console")


def test_sync_database_driver_is_rejected() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        build(DATABASE_URL="postgresql://user:pass@localhost:5432/db")


def test_the_test_double_provider_is_rejected_in_production() -> None:
    """Shipping the fake would mean serving canned text as model output."""
    with pytest.raises(ValidationError, match="must not be 'fake'"):
        build(LLM_PROVIDER="fake")


def test_the_test_double_embedder_is_rejected_in_production() -> None:
    """The fake embedder is lexical: retrieval against it is not retrieval."""
    with pytest.raises(ValidationError, match="EMBEDDING_PROVIDER must not be 'fake'"):
        build(EMBEDDING_PROVIDER="fake")


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    """Overlap >= size makes the chunker emit duplicates or never terminate."""
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
        build(CHUNK_SIZE_TOKENS=256, CHUNK_OVERLAP_TOKENS=256)


def test_placeholder_secret_is_allowed_outside_production() -> None:
    """Development must stay frictionless; only production is locked down."""
    settings = build(
        ENVIRONMENT=Environment.DEVELOPMENT,
        SECRET_KEY=PLACEHOLDER_SECRET,
        DEBUG=True,
        LOG_FORMAT="console",
        CORS_ORIGINS="*",
        LLM_PROVIDER="fake",
        EMBEDDING_PROVIDER="fake",
    )
    assert not settings.is_production


def test_cors_origins_parses_comma_separated_list() -> None:
    settings = build(CORS_ORIGINS="https://a.example, https://b.example ,")
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_secret_key_is_not_exposed_by_repr() -> None:
    assert VALID_SECRET not in repr(build())
