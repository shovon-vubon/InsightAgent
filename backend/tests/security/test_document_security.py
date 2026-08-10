"""Knowledge base isolation — security control S5.

The knowledge base is the first feature where one user's data is *searchable*, so
a scoping mistake leaks document content rather than just its existence. Retrieval
is the dangerous path: it does not go through `get_owned`, it runs raw SQL, and a
missing `user_id` predicate there would silently make every corpus global.

Every test here is written from the attacker's side: Mallory tries to read, cite,
or delete Alice's documents.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.llm.fake import FakeProvider
from app.services.ingestion import DocumentIngestionService
from tests.api.test_documents import REPORT, register_and_login

SECRET_REPORT = """# Project Redacted Internal Memo

## Acquisition

The board approved acquiring Contoso for 240 million dollars.
The announcement is embargoed until the fourth quarter.
"""


async def _alice_with_a_document(
    client: AsyncClient, ingestion_service: DocumentIngestionService
) -> tuple[dict[str, str], uuid.UUID]:
    headers = await register_and_login(client, "alice@example.com")
    upload = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("secret.md", SECRET_REPORT.encode(), "text/markdown")},
    )
    document_id = uuid.UUID(upload.json()["document_id"])
    await ingestion_service.process(document_id)
    return headers, document_id


class TestCrossUserIsolation:
    async def test_another_user_cannot_read_the_document(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        _, document_id = await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        response = await client.get(f"/api/v1/documents/{document_id}", headers=mallory)
        # 404 rather than 403: existence stays unobservable to a probing client.
        assert response.status_code == 404

    async def test_another_user_cannot_delete_the_document(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        alice, document_id = await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        assert (
            await client.delete(f"/api/v1/documents/{document_id}", headers=mallory)
        ).status_code == 404
        # Still there for its owner.
        assert (
            await client.get(f"/api/v1/documents/{document_id}", headers=alice)
        ).status_code == 200

    async def test_another_user_does_not_see_it_in_their_list(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        assert (await client.get("/api/v1/documents", headers=mallory)).json() == []

    async def test_retrieval_never_crosses_the_user_boundary(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
    ) -> None:
        """The important one: search must not reach another user's chunks."""
        await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        fake_llm.script("The acquisition was approved [1].")
        body = (
            await client.post(
                "/api/v1/documents/ask",
                headers=mallory,
                json={"question": "How much did the board approve for acquiring Contoso?"},
            )
        ).json()

        assert body["insufficient_evidence"] is True
        assert body["citations"] == []
        assert body["candidates_considered"] == 0
        assert "Contoso" not in body["answer"]

    async def test_document_id_filter_cannot_target_another_users_document(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
    ) -> None:
        """Naming the document id explicitly must not bypass ownership."""
        _, document_id = await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        fake_llm.script("Approved [1].")
        body = (
            await client.post(
                "/api/v1/documents/ask",
                headers=mallory,
                json={
                    "question": "What was approved for Contoso?",
                    "document_ids": [str(document_id)],
                },
            )
        ).json()

        assert body["insufficient_evidence"] is True
        assert body["citations"] == []

    async def test_stats_are_scoped_to_the_requesting_user(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        await _alice_with_a_document(client, ingestion_service)
        mallory = await register_and_login(client, "mallory@example.com")

        body = (await client.get("/api/v1/documents/stats", headers=mallory)).json()
        assert body["documents"] == 0
        assert body["total_chunks"] == 0

    async def test_dedupe_does_not_leak_another_users_upload(self, client: AsyncClient) -> None:
        """Uniqueness is per user, so dedupe cannot be used as an oracle.

        If the hash were globally unique, Mallory could upload a candidate file
        and learn from the `duplicate` flag whether Alice already had it.
        """
        alice = await register_and_login(client, "alice@example.com")
        await client.post(
            "/api/v1/documents",
            headers=alice,
            files={"file": ("shared.md", REPORT.encode(), "text/markdown")},
        )

        mallory = await register_and_login(client, "mallory@example.com")
        response = await client.post(
            "/api/v1/documents",
            headers=mallory,
            files={"file": ("shared.md", REPORT.encode(), "text/markdown")},
        )

        assert response.status_code == 201
        assert response.json()["duplicate"] is False


class TestStorageContainment:
    async def test_stored_paths_are_generated_not_client_supplied(
        self, client: AsyncClient
    ) -> None:
        headers = await register_and_login(client)
        response = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={
                "file": (
                    "../../../../etc/cron.d/evil.md",
                    b"# Heading\n\nSome body text here.\n",
                    "text/markdown",
                )
            },
        )
        assert response.status_code == 201
        # The display name is sanitised, and the path on disk is derived from the
        # document's UUID rather than from anything the client sent.
        assert response.json()["filename"] == "evil.md"

    async def test_store_refuses_to_resolve_outside_its_root(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        from app.core.exceptions import NotFoundError
        from app.rag.storage import DocumentStore

        store = DocumentStore(tmp_path_factory.mktemp("uploads"))
        store.ensure_ready()

        with pytest.raises(NotFoundError):
            store.resolve("../../../etc/passwd")
