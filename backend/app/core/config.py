"""Application settings.

Every value is environment-driven (brief §48). Secrets have no defaults so a
misconfigured deployment fails at startup rather than silently running with a
predictable key.
"""

from __future__ import annotations

import functools
from enum import StrEnum
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

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT is Environment.PRODUCTION

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
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Tests clear the cache via `get_settings.cache_clear()`."""
    return Settings()  # values come from the environment / .env
