"""Application settings.

Every value is environment-driven (brief §48). Secrets have no defaults so a
misconfigured deployment fails at startup rather than silently running with a
predictable key.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


# Value shipped in .env.example. Refusing to boot production with it is the whole
# point of having it be a recognisable constant.
PLACEHOLDER_SECRET = "change-me-generate-with-openssl-rand-hex-32"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- application ---------------------------------------------------------
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DEBUG: bool = False
    PROJECT_NAME: str = "InsightAgent"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    # --- security ------------------------------------------------------------
    SECRET_KEY: SecretStr
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = Field(default=900, ge=60, le=3600)
    REFRESH_TOKEN_TTL_SECONDS: int = Field(default=2_592_000, ge=3600)
    PASSWORD_MIN_LENGTH: int = Field(default=12, ge=8)

    # Comma-separated. Kept as a string because pydantic-settings JSON-decodes
    # list-typed fields before validators run, which makes `a,b` a parse error.
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- data plane ----------------------------------------------------------
    DATABASE_URL: str
    # Populated in Phase 5: the SELECT-only role the SQL agent authenticates as.
    DATABASE_URL_READONLY: str | None = None
    DB_POOL_SIZE: int = Field(default=10, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=5, ge=0)
    DB_ECHO: bool = False

    REDIS_URL: str = "redis://localhost:6379/0"

    # --- logging -------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # --- language models -----------------------------------------------------
    # Defaults to the deterministic test double so a fresh clone runs end to end
    # before any API key exists. The UI labels it; nothing pretends it is a model.
    LLM_PROVIDER: Literal["fake", "openai", "anthropic", "ollama"] = "fake"
    #: Blank uses the provider's own default model.
    LLM_MODEL: str | None = None
    LLM_TEMPERATURE: float = Field(default=0.2, ge=0.0, le=2.0)
    LLM_MAX_TOKENS: int = Field(default=1024, ge=64, le=32_000)
    LLM_TIMEOUT_SECONDS: float = Field(default=60.0, ge=5.0, le=600.0)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    #: How many prior messages are replayed as context on each chat turn.
    CHAT_HISTORY_LIMIT: int = Field(default=20, ge=2, le=100)

    OPENAI_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # --- embeddings ----------------------------------------------------------
    # Independent of LLM_PROVIDER: the model that answers and the model that
    # embeds are separate experiment variables (see D3), and in practice they are
    # rarely from the same vendor.
    EMBEDDING_PROVIDER: Literal["fake", "ollama", "openai"] = "fake"
    #: Blank uses the provider's own default model.
    EMBEDDING_MODEL: str | None = None
    EMBEDDING_BATCH_SIZE: int = Field(default=32, ge=1, le=512)
    EMBEDDING_TIMEOUT_SECONDS: float = Field(default=120.0, ge=5.0, le=600.0)
    #: Cache TTL for embeddings keyed by (model, content hash). Re-ingesting the
    #: same document, or asking the same question twice, then costs nothing.
    EMBEDDING_CACHE_TTL_SECONDS: int = Field(default=604_800, ge=0)

    # --- knowledge base ------------------------------------------------------
    #: Where uploaded originals are written. Outside any web-served directory,
    #: under generated names (S4).
    STORAGE_DIR: str = "/var/lib/insightagent/uploads"
    MAX_UPLOAD_BYTES: int = Field(default=25 * 1024 * 1024, ge=1024)
    MAX_DOCUMENTS_PER_USER: int = Field(default=100, ge=1)
    MAX_STORAGE_BYTES_PER_USER: int = Field(default=250 * 1024 * 1024, ge=1024)

    # --- chunking & retrieval ------------------------------------------------
    # Part of the retrieval config that Phase 4 hashes into `config_hash`, so
    # every measured number stays attributable to the settings that produced it.
    CHUNK_SIZE_TOKENS: int = Field(default=512, ge=64, le=2048)
    CHUNK_OVERLAP_TOKENS: int = Field(default=64, ge=0, le=512)
    RETRIEVAL_TOP_K: int = Field(default=8, ge=1, le=50)
    #: Candidates below this cosine similarity are dropped before synthesis, so a
    #: query with no real support retrieves nothing rather than the least-bad rows.
    RETRIEVAL_SCORE_FLOOR: float = Field(default=0.25, ge=0.0, le=1.0)

    @property
    def openai_api_key(self) -> str | None:
        return self.OPENAI_API_KEY.get_secret_value() if self.OPENAI_API_KEY else None

    @property
    def anthropic_api_key(self) -> str | None:
        return self.ANTHROPIC_API_KEY.get_secret_value() if self.ANTHROPIC_API_KEY else None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT is Environment.PRODUCTION

    @property
    def storage_path(self) -> Path:
        return Path(self.STORAGE_DIR)

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def _database_url_is_async(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver")
        return value

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> Settings:
        # Overlap >= chunk size makes the chunker either loop forever or emit
        # duplicate chunks; catching it here beats discovering it mid-ingestion.
        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_SIZE_TOKENS:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_SIZE_TOKENS")
        return self

    @model_validator(mode="after")
    def _production_hardening(self) -> Settings:
        if not self.is_production:
            return self
        if self.DEBUG:
            raise ValueError("DEBUG must be false in production")
        if self.SECRET_KEY.get_secret_value() == PLACEHOLDER_SECRET:
            raise ValueError("SECRET_KEY is still the .env.example placeholder")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS may not be '*' in production")
        if self.LOG_FORMAT != "json":
            raise ValueError("LOG_FORMAT must be 'json' in production")
        if self.LLM_PROVIDER == "fake":
            # Shipping the test double to production would mean serving canned
            # text as though it were model output.
            raise ValueError("LLM_PROVIDER must not be 'fake' in production")
        if self.EMBEDDING_PROVIDER == "fake":
            # The fake embedder is hash-based: retrieval against it is nonsense
            # dressed up as relevance, which is worse than an outage.
            raise ValueError("EMBEDDING_PROVIDER must not be 'fake' in production")
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache via `get_settings.cache_clear()`."""
    return Settings()  # values come from the environment / .env
