"""SQLAlchemy models.

Every model must be imported here: Alembic's autogenerate only sees tables that
have been registered on `Base.metadata` by import time.
"""

from app.db.base import Base
from app.models.conversation import Conversation, Message, MessageRole
from app.models.document import ChunkEmbedding, Document, DocumentChunk, DocumentStatus
from app.models.llm_call import LLMCall, LLMCallStatus
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "ChunkEmbedding",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "LLMCall",
    "LLMCallStatus",
    "Message",
    "MessageRole",
    "RefreshToken",
    "User",
    "UserRole",
]
