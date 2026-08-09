from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.refresh_token import RefreshToken


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"


# `native_enum=False` renders VARCHAR + CHECK rather than a PostgreSQL ENUM type.
# The role set is expected to grow (ANALYST), and altering a CHECK constraint in a
# migration is trivial where `ALTER TYPE ... ADD VALUE` is not.
UserRoleType = Enum(
    UserRole,
    native_enum=False,
    length=32,
    validate_strings=True,
    name="user_role",
)


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    # Stored lower-cased by the service layer, so a plain unique constraint is a
    # true case-insensitive uniqueness guarantee without needing citext.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), default=None)
    role: Mapped[UserRole] = mapped_column(UserRoleType, default=UserRole.USER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # passive_deletes: the database's ON DELETE CASCADE removes children, so the
    # ORM never has to load a collection that `lazy="raise"` forbids loading.
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} role={self.role}>"
