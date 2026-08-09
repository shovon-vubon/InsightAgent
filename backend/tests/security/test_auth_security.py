"""Security behaviour that must not regress.

These are the tests that would catch the failures that actually matter: a leaked
refresh token staying usable, a normal user reaching an admin route, or a query
returning another user's rows.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import REFRESH_COOKIE_NAME
from app.core.config import Settings
from app.core.security import create_access_token
from app.models.user import UserRole
from app.repositories.conversation import ConversationRepository
from app.services.auth import AuthService

AUTH = "/api/v1/auth"
PROBE = "/api/v1/_probe"
PASSWORD = "a-sufficiently-long-password"


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post(f"{AUTH}/register", json={"email": email, "password": PASSWORD})
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    return str(response.json()["access_token"])


def _replay(client: AsyncClient, token: str) -> dict[str, str]:
    """Send a specific refresh token, bypassing whatever the cookie jar holds."""
    client.cookies.clear()
    return {"Cookie": f"{REFRESH_COOKIE_NAME}={token}"}


class TestRefreshTokenReuse:
    async def test_replaying_a_rotated_token_is_rejected(self, client: AsyncClient) -> None:
        await _register_and_login(client, "reuse@example.com")
        stolen = client.cookies.get(REFRESH_COOKIE_NAME)
        assert stolen

        assert (await client.post(f"{AUTH}/refresh")).status_code == 200

        replayed = await client.post(f"{AUTH}/refresh", headers=_replay(client, stolen))
        assert replayed.status_code == 401

    async def test_replay_revokes_the_whole_family(self, client: AsyncClient) -> None:
        """The legitimate holder is logged out too — that is the intended trade.

        Once a token from the chain has demonstrably leaked, there is no way to tell
        the attacker's copy from the user's, so every descendant is invalidated.
        """
        await _register_and_login(client, "family@example.com")
        stolen = client.cookies.get(REFRESH_COOKIE_NAME)
        assert stolen

        rotated = await client.post(f"{AUTH}/refresh")
        current = rotated.cookies.get(REFRESH_COOKIE_NAME)
        assert current

        await client.post(f"{AUTH}/refresh", headers=_replay(client, stolen))

        still_valid = await client.post(f"{AUTH}/refresh", headers=_replay(client, current))
        assert still_valid.status_code == 401

    async def test_an_unknown_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.post(f"{AUTH}/refresh", headers=_replay(client, "not-a-real-token"))
        assert response.status_code == 401


class TestAuthorization:
    async def test_authenticated_user_reaches_a_protected_route(self, client: AsyncClient) -> None:
        token = await _register_and_login(client, "member@example.com")
        response = await client.get(
            f"{PROBE}/authenticated", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    async def test_normal_user_is_forbidden_from_an_admin_route(self, client: AsyncClient) -> None:
        token = await _register_and_login(client, "notadmin@example.com")

        response = await client.get(
            f"{PROBE}/admin-only", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    async def test_anonymous_request_is_unauthorised_not_forbidden(
        self, client: AsyncClient
    ) -> None:
        assert (await client.get(f"{PROBE}/admin-only")).status_code == 401

    async def test_admin_reaches_the_admin_route(
        self, client: AsyncClient, db_session: AsyncSession, settings: Settings
    ) -> None:
        admin = await AuthService(db_session, settings).ensure_admin(
            email="admin@example.com", password=PASSWORD
        )
        await db_session.flush()

        response = await client.post(
            f"{AUTH}/login", json={"email": "admin@example.com", "password": PASSWORD}
        )
        token = response.json()["access_token"]

        result = await client.get(
            f"{PROBE}/admin-only", headers={"Authorization": f"Bearer {token}"}
        )

        assert result.status_code == 200
        assert result.json()["user_id"] == str(admin.id)

    async def test_role_claim_in_the_token_cannot_grant_admin(
        self, client: AsyncClient, db_session: AsyncSession, settings: Settings
    ) -> None:
        """Privilege comes from the database row, never from the token payload."""
        user = await AuthService(db_session, settings).register(
            email="claims@example.com", password=PASSWORD
        )
        await db_session.flush()

        forged, _ = create_access_token(settings, subject=user.id, role=UserRole.ADMIN.value)

        response = await client.get(
            f"{PROBE}/admin-only", headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 403

    async def test_deactivated_account_loses_access_before_token_expiry(
        self, client: AsyncClient, db_session: AsyncSession, settings: Settings
    ) -> None:
        user = await AuthService(db_session, settings).register(
            email="disabled@example.com", password=PASSWORD
        )
        token, _ = create_access_token(settings, subject=user.id, role=user.role.value)
        await db_session.flush()

        user.is_active = False
        await db_session.flush()

        response = await client.get(
            f"{PROBE}/authenticated", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    async def test_token_for_a_deleted_user_is_rejected(
        self, client: AsyncClient, settings: Settings
    ) -> None:
        token, _ = create_access_token(settings, subject=uuid.uuid4(), role="ADMIN")
        response = await client.get(
            f"{PROBE}/authenticated", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


class TestOwnershipScoping:
    """The IDOR guard, tested at the repository layer where it is enforced."""

    @pytest.fixture
    async def two_users_with_conversations(
        self, db_session: AsyncSession, settings: Settings
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        service = AuthService(db_session, settings)
        alice = await service.register(email="alice@example.com", password=PASSWORD)
        bob = await service.register(email="bob@example.com", password=PASSWORD)

        repo = ConversationRepository(db_session)
        alice_conversation = await repo.create(user_id=alice.id, title="Alice's research")
        await repo.create(user_id=bob.id, title="Bob's research")

        return alice.id, bob.id, alice_conversation.id

    async def test_owner_can_read_their_own_row(
        self, db_session: AsyncSession, two_users_with_conversations: tuple[uuid.UUID, ...]
    ) -> None:
        alice_id, _, conversation_id = two_users_with_conversations
        repo = ConversationRepository(db_session)

        assert await repo.get_owned(conversation_id, alice_id) is not None

    async def test_another_user_cannot_read_it(
        self, db_session: AsyncSession, two_users_with_conversations: tuple[uuid.UUID, ...]
    ) -> None:
        _, bob_id, conversation_id = two_users_with_conversations
        repo = ConversationRepository(db_session)

        assert await repo.get_owned(conversation_id, bob_id) is None

    async def test_listing_never_crosses_owners(
        self, db_session: AsyncSession, two_users_with_conversations: tuple[uuid.UUID, ...]
    ) -> None:
        alice_id, bob_id, _ = two_users_with_conversations
        repo = ConversationRepository(db_session)

        alice_titles = [c.title for c in await repo.list_for_user(alice_id)]
        bob_titles = [c.title for c in await repo.list_for_user(bob_id)]

        assert alice_titles == ["Alice's research"]
        assert bob_titles == ["Bob's research"]
