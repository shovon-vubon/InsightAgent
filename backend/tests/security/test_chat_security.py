"""Conversations are user-owned data. These are the tests that catch a leak."""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_call import LLMCall

API = "/api/v1"
PASSWORD = "a-sufficiently-long-password"


async def user_token(client: AsyncClient, email: str) -> str:
    await client.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    client.cookies.clear()  # keep sessions from bleeding between the two users
    return str(response.json()["access_token"])


def header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestConversationIsolation:
    async def test_another_user_cannot_read_a_conversation(self, client: AsyncClient) -> None:
        alice = await user_token(client, "alice.chat@example.com")
        created = await client.post(
            f"{API}/conversations", json={"title": "private"}, headers=header(alice)
        )
        conversation_id = created.json()["id"]

        bob = await user_token(client, "bob.chat@example.com")
        response = await client.get(f"{API}/conversations/{conversation_id}", headers=header(bob))

        # 404, not 403: whether the row exists is itself information.
        assert response.status_code == 404

    async def test_another_user_cannot_delete_a_conversation(self, client: AsyncClient) -> None:
        alice = await user_token(client, "alice.del@example.com")
        created = await client.post(
            f"{API}/conversations", json={"title": "private"}, headers=header(alice)
        )
        conversation_id = created.json()["id"]

        bob = await user_token(client, "bob.del@example.com")
        assert (
            await client.delete(f"{API}/conversations/{conversation_id}", headers=header(bob))
        ).status_code == 404

        alice_view = await client.get(
            f"{API}/conversations/{conversation_id}", headers=header(alice)
        )
        assert alice_view.status_code == 200, "the owner's data must be untouched"

    async def test_another_user_cannot_post_into_a_conversation(self, client: AsyncClient) -> None:
        alice = await user_token(client, "alice.post@example.com")
        created = await client.post(
            f"{API}/conversations", json={"title": "private"}, headers=header(alice)
        )
        conversation_id = created.json()["id"]

        bob = await user_token(client, "bob.post@example.com")
        response = await client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": "smuggled in"},
            headers=header(bob),
        )

        payload = json.loads(response.text.strip().split("data: ")[-1])
        assert payload["code"] == "not_found"

    async def test_listing_only_returns_your_own(self, client: AsyncClient) -> None:
        alice = await user_token(client, "alice.list@example.com")
        await client.post(
            f"{API}/conversations", json={"title": "alice only"}, headers=header(alice)
        )

        bob = await user_token(client, "bob.list@example.com")
        await client.post(f"{API}/conversations", json={"title": "bob only"}, headers=header(bob))

        bob_list = await client.get(f"{API}/conversations", headers=header(bob))
        assert [item["title"] for item in bob_list.json()] == ["bob only"]


class TestUsageAttribution:
    async def test_calls_are_attributed_to_the_requesting_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await user_token(client, "attribution@example.com")
        me = await client.get(f"{API}/auth/me", headers=header(token))
        user_id = me.json()["id"]

        created = await client.post(
            f"{API}/conversations", json={"title": "spend"}, headers=header(token)
        )
        await client.post(
            f"{API}/conversations/{created.json()['id']}/messages",
            json={"content": "hello"},
            headers=header(token),
        )

        call = (await db_session.execute(select(LLMCall))).scalars().one()
        assert str(call.user_id) == user_id


class TestAdminUsageEndpoint:
    async def test_a_normal_user_is_forbidden(self, client: AsyncClient) -> None:
        token = await user_token(client, "normal.usage@example.com")
        response = await client.get(f"{API}/admin/usage", headers=header(token))
        assert response.status_code == 403

    async def test_anonymous_is_unauthorised(self, client: AsyncClient) -> None:
        assert (await client.get(f"{API}/admin/usage")).status_code == 401
