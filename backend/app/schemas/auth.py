from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole

# NIST SP 800-63B favours length over composition rules, so there is no
# "must contain a symbol" check. The upper bound guards against a large-input
# argon2 DoS.
PASSWORD_MAX_LENGTH = 128


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=PASSWORD_MAX_LENGTH)
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _reject_whitespace_only_padding(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password must not be blank")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class AccessToken(BaseModel):
    """Only the access token crosses in the response body.

    The refresh token is delivered as an HttpOnly cookie scoped to the auth path,
    so JavaScript — and therefore an XSS payload — cannot read it.
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - RFC 6750 scheme name, not a credential
    expires_at: datetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
