"""SQLAlchemy models.

Every model must be imported here: Alembic's autogenerate only sees tables that
have been registered on `Base.metadata` by import time.
"""

from app.db.base import Base
from app.models.conversation import Conversation, Message, MessageRole
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "Conversation",
    "Message",
    "MessageRole",
    "RefreshToken",
    "User",
    "UserRole",
]
