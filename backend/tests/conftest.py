"""Test fixtures.

Tests run against a real PostgreSQL and Redis â€” the schema, constraints, and
transaction behaviour are exactly what this phase is meant to prove, and none of
that survives being mocked.

The test database is created once per session and migrated with the *real*
`alembic upgrade head`, so a model that has drifted from its migration fails the
suite rather than passing against a `create_all` schema that nobody ships.

Each test then runs inside a transaction that is rolled back afterwards, which
keeps tests isolated and fast without re-migrating between them.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import (
    AdminUser,
    CurrentUser,
    get_database,
    get_db,
    get_llm_provider,
    get_redis,
)
from app.cache.redis import create_redis
from app.core.config import Environment, Settings
from app.db.session import Database
from app.llm.fake import FakeProvider
from app.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "insightagent_test"

DEFAULT_ADMIN_URL = "postgresql+asyncpg://insightagent:local_dev_pg_pw@localhost:5432/insightagent"
DEFAULT_REDIS_URL = "redis://localhost:6379/1"
TEST_SECRET_KEY = "test-secret-key-not-used-anywhere-real-0123456789"


def _base_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", DEFAULT_ADMIN_URL)


def _with_database(url: str, database: str) -> str:
    parts = urlparse(url)
    return urlunparse(parts._replace(path=f"/{database}"))


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _recreate_test_database(admin_url: str) -> None:
    """Drop and recreate the test database so every run starts from nothing."""
    conn = await asyncpg.connect(_to_asyncpg_dsn(admin_url))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _install_extensions(test_url: str) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(test_url))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Provision a migrated, throwaway database for the session."""
    admin_url = _base_database_url()
    test_url = _with_database(admin_url, TEST_DB_NAME)

    try:
        asyncio.run(_recreate_test_database(admin_url))
        asyncio.run(_install_extensions(test_url))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")

    # A subprocess, not `alembic.command`: env.py calls asyncio.run(), which cannot
    # be nested inside pytest-asyncio's loop.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": test_url},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")

    yield test_url


@pytest.fixture(scope="session")
def settings(test_database_url: str) -> Settings:
    return Settings(
        ENVIRONMENT=Environment.TESTING,
        DEBUG=True,
        SECRET_KEY=TEST_SECRET_KEY,
        DATABASE_URL=test_database_url,
        REDIS_URL=os.environ.get("TEST_REDIS_URL", DEFAULT_REDIS_URL),
        LOG_LEVEL="WARNING",
        LOG_FORMAT="console",
    )


@pytest.fixture(scope="session")
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    # NullPool: connections must not outlive a test's transaction.
    async_engine = create_async_engine(test_database_url, poolclass=NullPool)
    yield async_engine
    await async_engine.dispose()


@pytest.fixture
async def db_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection per test, in a transaction that is always rolled back.

    Everything in a test shares this single connection. `ChatService` opens its
    own sessions and commits them, so if it ran on a second connection it could
    not see a conversation the API had created but not yet committed.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()


@pytest.fixture
def session_factory(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """Sessions joined to the test transaction.

    `join_transaction_mode="create_savepoint"` means application code calling
    `session.commit()` - as the refresh-token replay path and every chat write
    deliberately do - releases a savepoint rather than committing for real, so the
    outer rollback still cleans up.
    """
    return async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def test_database(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> Database:
    """`Database` for services that manage their own units of work."""
    return Database(engine=engine, session_factory=session_factory)


@pytest.fixture
def fake_llm() -> FakeProvider:
    # Zero backoff: tests must not sleep through retries.
    return FakeProvider(retry_base_delay=0.0)


@pytest.fixture
async def redis_client(settings: Settings) -> AsyncIterator[Redis]:
    client = create_redis(settings)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis is not reachable for integration tests")
    await client.flushdb()
    yield client
    await client.aclose()


def _register_probe_routes(application: FastAPI) -> None:
    """Minimal routes that exercise the auth dependencies directly.

    Phase 1 ships no admin endpoints, but `require_admin` and `get_current_user`
    are security controls that must be tested now rather than when the first admin
    feature lands. These live only in the test harness.
    """

    @application.get("/api/v1/_probe/authenticated")
    async def probe_authenticated(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.id)}

    @application.get("/api/v1/_probe/admin-only")
    async def probe_admin_only(user: AdminUser) -> dict[str, str]:
        return {"user_id": str(user.id)}


@pytest.fixture
def app(
    settings: Settings,
    db_session: AsyncSession,
    test_database: Database,
    redis_client: Redis,
    fake_llm: FakeProvider,
) -> FastAPI:
    application = create_app(settings)
    _register_probe_routes(application)

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_database] = lambda: test_database
    application.dependency_overrides[get_redis] = lambda: redis_client
    application.dependency_overrides[get_llm_provider] = lambda: fake_llm
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
