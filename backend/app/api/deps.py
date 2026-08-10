"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token
from app.db.session import Database
from app.llm.base import LLMProvider
from app.models.user import User
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.storage import DocumentStore
from app.repositories.user import UserRepository
from app.services.ingestion import DocumentIngestionService
from app.services.knowledge import KnowledgeService

# auto_error=False so a missing header raises our own exception type and produces
# the same error envelope as everything else.
bearer_scheme = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_llm_provider(request: Request) -> LLMProvider:
    provider: LLMProvider = request.app.state.llm_provider
    return provider


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    provider: EmbeddingProvider = request.app.state.embedding_provider
    return provider


def get_document_store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.document_store
    return store


def get_ingestion_service(request: Request) -> DocumentIngestionService:
    """Built per request: it is a thin façade over shared, long-lived objects.

    The queue may be `None` when Redis was unreachable at startup. Uploads then
    still succeed and stay `UPLOADED` rather than the endpoint failing — the
    document is safe on disk and can be processed once a worker is available.
    """
    return DocumentIngestionService(
        database=request.app.state.database,
        settings=request.app.state.settings,
        store=request.app.state.document_store,
        embedder=request.app.state.embedding_provider,
        queue=getattr(request.app.state, "task_queue", None),
    )


def get_knowledge_service(request: Request) -> KnowledgeService:
    return KnowledgeService(
        database=request.app.state.database,
        settings=request.app.state.settings,
        provider=request.app.state.llm_provider,
        embedder=request.app.state.embedding_provider,
    )


async def get_db(
    request: Request,
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """One transaction per request: rolls back on error, commits on success.

    The commit is performed by `CommittingRoute`, not here. A dependency with
    `yield` has its teardown run *after* the response is sent, so a commit placed
    here is not guaranteed to be visible to whatever the client does next — see
    `app.api.route` for the measurement. The session is published on
    `request.state` for the route handler to find.
    """
    async with database.session_factory() as session:
        request.state.db_session = session
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            # Belt and braces: if this session somehow reaches teardown still
            # holding an open transaction — a route class other than
            # CommittingRoute, or a test calling the dependency directly — the
            # work is committed rather than silently discarded on close.
            if session.in_transaction():
                await session.commit()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token.")

    try:
        payload = decode_access_token(settings, credentials.credentials)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid token subject.") from exc

    # The user is re-read on every request rather than trusted from the claims, so
    # deactivating an account takes effect immediately instead of at token expiry.
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is unavailable.")
    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_admin:
        raise AuthorizationError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
DocumentStoreDep = Annotated[DocumentStore, Depends(get_document_store)]
IngestionServiceDep = Annotated[DocumentIngestionService, Depends(get_ingestion_service)]
KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]
