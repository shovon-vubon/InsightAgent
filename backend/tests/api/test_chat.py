"""Conversation CRUD and the streaming chat turn."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.errors import LLMRateLimitError
from app.llm.fake import FakeProvider
from app.models.conversation import Message, MessageRole
from app.models.llm_call import LLMCall, LLMCallStatus

API = "/api/v1"
PASSWORD = "a-sufficiently-long-password"


async def authenticate(client: AsyncClient, email: str = "chat@example.com") -> str:
    await client.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    return str(response.json()["access_token"])


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into (event, data) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.strip().split("\n\n"):
        if not frame.strip():
            continue
        name = ""
        payload = "{}"
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = line.removeprefix("data: ")
        events.append((name, json.loads(payload)))
    return events


async def start_conversation(client: AsyncClient, token: str) -> str:
    response = await client.post(
        f"{API}/conversations", json={"title": "Q2 analysis"}, headers=auth_header(token)
    )
    assert response.status_code == 201
    return str(response.json()["id"])


async def send(client: AsyncClient, token: str, conversation_id: str, content: str) -> str:
    response = await client.post(
        f"{API}/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=auth_header(token),
    )
    assert response.status_code == 200
    return response.text


class TestConversationCrud:
    async def test_create_and_list(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        await start_conversation(client, token)

        response = await client.get(f"{API}/conversations", headers=auth_header(token))

        assert response.status_code == 200
        assert [item["title"] for item in response.json()] == ["Q2 analysis"]

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(f"{API}/conversations")).status_code == 401

    async def test_fetch_includes_messages_in_order(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)
        await send(client, token, conversation_id, "first question")

        response = await client.get(
            f"{API}/conversations/{conversation_id}", headers=auth_header(token)
        )

        messages = response.json()["messages"]
        assert [m["role"] for m in messages] == ["USER", "ASSISTANT"]
        assert [m["sequence"] for m in messages] == [1, 2]

    async def test_delete_removes_it(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        assert (
            await client.delete(
                f"{API}/conversations/{conversation_id}", headers=auth_header(token)
            )
        ).status_code == 204
        assert (
            await client.get(f"{API}/conversations/{conversation_id}", headers=auth_header(token))
        ).status_code == 404

    async def test_unknown_conversation_is_404(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        response = await client.get(
            f"{API}/conversations/00000000-0000-0000-0000-000000000000",
            headers=auth_header(token),
        )
        assert response.status_code == 404


class TestStreaming:
    async def test_emits_user_message_then_deltas_then_done(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        events = parse_sse(await send(client, token, conversation_id, "why did revenue fall"))
        names = [name for name, _ in events]

        assert names[0] == "user_message"
        assert names[-1] == "done"
        assert names.count("delta") > 1, "the response should arrive incrementally"
        assert "error" not in names

    async def test_deltas_reassemble_into_the_stored_message(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        events = parse_sse(await send(client, token, conversation_id, "explain churn"))
        streamed = "".join(data["text"] for name, data in events if name == "delta")

        stored = (
            (await db_session.execute(select(Message).where(Message.role == MessageRole.ASSISTANT)))
            .scalars()
            .all()
        )

        assert len(stored) == 1
        assert stored[0].content == streamed

    async def test_done_event_carries_usage_and_provider_identity(
        self, client: AsyncClient
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        events = parse_sse(await send(client, token, conversation_id, "hello"))
        done = next(data for name, data in events if name == "done")

        assert done["provider"] == "fake"
        assert done["is_test_double"] is True, "the UI must be able to label canned output"
        assert done["input_tokens"] > 0
        assert done["output_tokens"] > 0
        assert done["cost_usd"] is not None

    async def test_first_message_becomes_the_conversation_title(self, client: AsyncClient) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        await send(client, token, conversation_id, "Compare Q1 and Q2 churn")

        response = await client.get(
            f"{API}/conversations/{conversation_id}", headers=auth_header(token)
        )
        assert response.json()["title"] == "Compare Q1 and Q2 churn"

    async def test_history_is_replayed_to_the_provider(
        self, client: AsyncClient, fake_llm: FakeProvider
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        await send(client, token, conversation_id, "first question")
        await send(client, token, conversation_id, "second question")

        last_call = fake_llm.calls[-1]
        contents = [message.content for message in last_call]
        assert any("first question" in item for item in contents)
        assert any("second question" in item for item in contents)
        assert last_call[0].role.value == "system"

    @pytest.mark.parametrize("content", ["", "   "])
    async def test_blank_messages_are_rejected(self, client: AsyncClient, content: str) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        response = await client.post(
            f"{API}/conversations/{conversation_id}/messages",
            json={"content": content},
            headers=auth_header(token),
        )
        assert response.status_code == 422

    async def test_sending_to_an_unknown_conversation_streams_an_error(
        self, client: AsyncClient
    ) -> None:
        """The status is already 200 by the time this is known, so it must be an event."""
        token = await authenticate(client)

        body = await send(client, token, "00000000-0000-0000-0000-000000000000", "hello")
        events = parse_sse(body)

        assert events[0][0] == "error"
        assert events[0][1]["code"] == "not_found"


class TestFailureHandling:
    async def test_provider_failure_becomes_an_error_event(
        self, client: AsyncClient, fake_llm: FakeProvider
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)
        fake_llm.fail_next(99, LLMRateLimitError())

        events = parse_sse(await send(client, token, conversation_id, "hello"))
        names = [name for name, _ in events]

        assert names[-1] == "error"
        assert next(data for name, data in events if name == "error")["code"] == "llm_rate_limited"

    async def test_the_user_message_survives_a_provider_failure(
        self, client: AsyncClient, fake_llm: FakeProvider, db_session: AsyncSession
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)
        fake_llm.fail_next(99, LLMRateLimitError())

        await send(client, token, conversation_id, "do not lose this")

        stored = (
            (await db_session.execute(select(Message).where(Message.role == MessageRole.USER)))
            .scalars()
            .all()
        )
        assert [message.content for message in stored] == ["do not lose this"]

    async def test_failures_are_recorded_not_swallowed(
        self, client: AsyncClient, fake_llm: FakeProvider, db_session: AsyncSession
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)
        fake_llm.fail_next(99, LLMRateLimitError())

        await send(client, token, conversation_id, "hello")

        calls = (await db_session.execute(select(LLMCall))).scalars().all()
        assert len(calls) == 1
        assert calls[0].status is LLMCallStatus.FAILED
        assert calls[0].error_code == "llm_rate_limited"


class TestUsageAccounting:
    async def test_a_successful_turn_records_one_priced_call(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        await send(client, token, conversation_id, "hello")

        calls = (await db_session.execute(select(LLMCall))).scalars().all()
        assert len(calls) == 1
        call = calls[0]
        assert call.status is LLMCallStatus.SUCCEEDED
        assert call.provider == "fake"
        assert call.input_tokens > 0
        assert call.output_tokens > 0
        assert call.cost_usd is not None
        assert call.streamed is True

    async def test_the_prompt_version_is_recorded(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Without this, an evaluation result cannot be traced to its prompt."""
        token = await authenticate(client)
        conversation_id = await start_conversation(client, token)

        await send(client, token, conversation_id, "hello")

        call = (await db_session.execute(select(LLMCall))).scalars().one()
        assert call.prompt_name == "chat_system"
        assert call.prompt_version == "v1"
        assert call.prompt_checksum


class TestProviderInfoEndpoint:
    async def test_reports_the_active_provider(self, client: AsyncClient) -> None:
        token = await authenticate(client)

        response = await client.get(f"{API}/llm/provider", headers=auth_header(token))

        assert response.status_code == 200
        assert response.json() == {
            "provider": "fake",
            "model": "fake-1",
            "is_test_double": True,
        }
