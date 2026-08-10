"""Writes must be committed before the response is sent.

A dependency with `yield` has its teardown run *after* FastAPI sends the response,
so a commit placed there is not guaranteed to be visible to whatever the client
does next. Measured against the running stack before the fix,
`POST /auth/register` immediately followed by `POST /auth/login` failed 5 times
out of 5 with `reason=unknown_email`, while inserting a 50 ms pause made it pass.

`CommittingRoute` moves the commit into the route handler, which runs strictly
before the response leaves.

These tests use a second, independent database session to check visibility. That
is the point: the request-scoped session would see its own uncommitted writes and
report success either way, which is exactly why the bug survived Phase 1 and
Phase 2's suites.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.conversation import Conversation
from app.models.document import Document
from app.models.user import User
from tests.api.test_documents import REPORT, register_and_login


async def _count(
    session_factory: async_sessionmaker[AsyncSession], model: type, **filters: object
) -> int:
    """Count rows from a session other than the one the request used."""
    async with session_factory() as observer:
        stmt = select(func.count()).select_from(model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        result = await observer.execute(stmt)
        return int(result.scalar_one())


class TestCommitVisibility:
    async def test_registration_is_visible_to_another_session(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        email = f"commit-{uuid.uuid4().hex[:8]}@example.com"
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-horse-battery", "full_name": "C"},
        )
        assert response.status_code == 201

        # No sleep: the row must be committed by the time the response arrived.
        assert await _count(session_factory, User, email=email) == 1

    async def test_register_then_login_immediately(self, client: AsyncClient) -> None:
        """The exact sequence that failed 5/5 against the live stack."""
        email = f"seq-{uuid.uuid4().hex[:8]}@example.com"
        password = "correct-horse-battery"

        registered = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": "S"},
        )
        assert registered.status_code == 201

        logged_in = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert logged_in.status_code == 200, logged_in.text

    async def test_conversation_is_committed_before_the_response(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        headers = await register_and_login(client, "conv-commit@example.com")
        response = await client.post(
            "/api/v1/conversations", headers=headers, json={"title": "Committed"}
        )
        assert response.status_code == 201

        conversation_id = uuid.UUID(response.json()["id"])
        assert await _count(session_factory, Conversation, id=conversation_id) == 1

    async def test_upload_is_committed_before_the_response(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Without this the UI could poll for a document that is not yet there."""
        headers = await register_and_login(client, "upload-commit@example.com")
        response = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("q2.md", REPORT.encode(), "text/markdown")},
        )
        assert response.status_code == 201

        document_id = uuid.UUID(response.json()["document_id"])
        assert await _count(session_factory, Document, id=document_id) == 1

    async def test_failed_write_is_rolled_back(
        self, client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Committing early must not commit work the endpoint then rejected."""
        email = f"dupe-{uuid.uuid4().hex[:8]}@example.com"
        payload = {"email": email, "password": "correct-horse-battery", "full_name": "D"}

        assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
        # The second registration conflicts and must leave exactly one row.
        assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 409

        assert await _count(session_factory, User, email=email) == 1
