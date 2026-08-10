"""Knowledge base API, end to end against a real PostgreSQL with pgvector.

`test_upload_ingest_and_ask_cites_the_right_page` is the Phase 3 acceptance
criterion expressed as a test: upload a document, ingest it, ask a question, and
get back an answer citing the page the fact is actually on.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.llm.fake import FakeProvider
from app.models.document import DocumentStatus
from app.services.ingestion import DocumentIngestionService

REPORT = """# NovaRetail Q2 Financial Review

## Revenue

Total revenue for Q2 was 4.2 million dollars. This represents a decline of
12 percent compared with Q1. The decline was concentrated in the enterprise
segment.

## EMEA Performance

EMEA revenue held flat at 1.1 million dollars. Churn in the mid-market segment
rose to 4.1 percent, the highest level recorded in two years.

## Marketing Spend

Marketing spend totalled 380 thousand dollars, down 8 percent quarter on quarter.
The reduction came almost entirely from paid search.
"""


async def register_and_login(client: AsyncClient, email: str = "kb@example.com") -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery", "full_name": "KB User"},
    )
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def upload(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    content: bytes = REPORT.encode(),
    filename: str = "q2-review.md",
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": (filename, content, "text/markdown")},
    )
    return {"status_code": response.status_code, **response.json()}


class TestUpload:
    async def test_upload_returns_immediately_as_uploaded(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        result = await upload(client, headers)

        assert result["status_code"] == 201
        assert result["status"] == DocumentStatus.UPLOADED
        assert result["filename"] == "q2-review.md"
        assert result["duplicate"] is False

    async def test_identical_content_deduplicates(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        first = await upload(client, headers)
        # Same bytes, different filename: dedupe is content-addressed.
        second = await upload(client, headers, filename="a-copy.md")

        assert second["duplicate"] is True
        assert second["document_id"] == first["document_id"]

    async def test_rejects_a_binary_disguised_as_text(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        result = await upload(client, headers, content=b"\x7fELF\x02\x01" + b"\x00" * 200)
        assert result["status_code"] == 422

    async def test_rejects_an_unsupported_extension(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        result = await upload(client, headers, filename="payload.exe")
        assert result["status_code"] == 422

    async def test_rejects_an_empty_file(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        result = await upload(client, headers, content=b"")
        assert result["status_code"] == 422

    async def test_traversal_filename_is_stored_sanitised(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        result = await upload(client, headers, filename="../../../etc/passwd.md")
        assert result["filename"] == "passwd.md"


class TestIngestion:
    async def test_processing_produces_ready_and_chunks(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        document_id = uuid.UUID(uploaded["document_id"])

        await ingestion_service.process(document_id)

        response = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
        body = response.json()
        assert body["status"] == DocumentStatus.READY
        assert body["chunk_count"] > 0
        assert body["title"] == "NovaRetail Q2 Financial Review"
        assert body["error"] is None

    async def test_unparseable_document_fails_with_a_reason(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        headers = await register_and_login(client)
        # Valid text that yields nothing indexable once cleaned.
        uploaded = await upload(client, headers, content=b"   \n\n   \n")
        document_id = uuid.UUID(uploaded["document_id"])

        await ingestion_service.process(document_id)

        body = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
        assert body["status"] == DocumentStatus.FAILED
        assert body["error"]

    async def test_reprocessing_replaces_rather_than_duplicates_chunks(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        document_id = uuid.UUID(uploaded["document_id"])

        await ingestion_service.process(document_id)
        first = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
        await ingestion_service.process(document_id)
        second = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()

        assert second["chunk_count"] == first["chunk_count"]


class TestAsk:
    async def test_upload_ingest_and_ask_cites_the_right_page(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
    ) -> None:
        """The Phase 3 acceptance criterion."""
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        await ingestion_service.process(uuid.UUID(uploaded["document_id"]))

        # The deterministic provider cites source 1, which is whatever retrieval
        # actually ranked first — so the assertion tests retrieval, not the model.
        fake_llm.script("EMEA revenue held flat at 1.1 million dollars [1].")

        response = await client.post(
            "/api/v1/documents/ask",
            headers=headers,
            json={"question": "What happened to EMEA revenue and churn?"},
        )
        body = response.json()

        assert response.status_code == 200
        assert body["insufficient_evidence"] is False
        assert body["invalid_markers"] == []
        assert len(body["citations"]) == 1

        citation = body["citations"][0]
        assert citation["marker"] == 1
        assert citation["document_id"] == uploaded["document_id"]
        # Retrieval found the EMEA section rather than an arbitrary chunk.
        assert "EMEA" in citation["quote"]
        assert citation["section_path"] is not None
        assert "EMEA" in citation["section_path"]
        assert 0.0 <= citation["score"] <= 1.0

    async def test_unanswerable_question_returns_explicit_insufficiency(
        self, client: AsyncClient, ingestion_service: DocumentIngestionService
    ) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        await ingestion_service.process(uuid.UUID(uploaded["document_id"]))

        response = await client.post(
            "/api/v1/documents/ask",
            headers=headers,
            json={"question": "zzzz qqqq xxxx unrelated gibberish tokens"},
        )
        body = response.json()

        assert body["insufficient_evidence"] is True
        assert body["citations"] == []
        assert "does not contain" in body["answer"]

    async def test_empty_knowledge_base_does_not_fabricate(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        response = await client.post(
            "/api/v1/documents/ask",
            headers=headers,
            json={"question": "What was Q2 revenue?"},
        )
        body = response.json()
        assert body["insufficient_evidence"] is True
        assert body["citations"] == []

    async def test_fabricated_citation_is_stripped_and_reported(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
    ) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        await ingestion_service.process(uuid.UUID(uploaded["document_id"]))

        # The model invents a source that was never supplied.
        fake_llm.script("Revenue fell [1]. Margins improved sharply [99].")

        response = await client.post(
            "/api/v1/documents/ask",
            headers=headers,
            json={"question": "What happened to revenue?"},
        )
        body = response.json()

        assert body["invalid_markers"] == [99]
        assert "[99]" not in body["answer"]
        assert all(c["marker"] != 99 for c in body["citations"])

    async def test_ask_records_usage(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
    ) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)
        await ingestion_service.process(uuid.UUID(uploaded["document_id"]))
        fake_llm.script("Revenue fell 12 percent [1].")

        body = (
            await client.post(
                "/api/v1/documents/ask",
                headers=headers,
                json={"question": "What happened to revenue?"},
            )
        ).json()

        assert body["input_tokens"] > 0
        assert body["output_tokens"] > 0
        assert body["is_test_double"] is True

    async def test_blank_question_is_rejected(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        response = await client.post(
            "/api/v1/documents/ask", headers=headers, json={"question": "   "}
        )
        assert response.status_code == 422


class TestListingAndDeletion:
    async def test_list_returns_the_users_documents(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        await upload(client, headers)

        response = await client.get("/api/v1/documents", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_stats_reports_the_embedding_model(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        await upload(client, headers)

        body = (await client.get("/api/v1/documents/stats", headers=headers)).json()
        assert body["documents"] == 1
        assert body["embedding_model"] == "fake-embed-1"
        assert body["embedding_dimensions"] == 256
        assert body["is_test_double"] is True

    async def test_delete_removes_the_document(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        uploaded = await upload(client, headers)

        response = await client.delete(
            f"/api/v1/documents/{uploaded['document_id']}", headers=headers
        )
        assert response.status_code == 204

        follow_up = await client.get(
            f"/api/v1/documents/{uploaded['document_id']}", headers=headers
        )
        assert follow_up.status_code == 404

    async def test_deleting_a_missing_document_is_404(self, client: AsyncClient) -> None:
        headers = await register_and_login(client)
        response = await client.delete(f"/api/v1/documents/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/v1/documents"),
            ("GET", "/api/v1/documents/stats"),
            ("POST", "/api/v1/documents/ask"),
        ],
    )
    async def test_endpoints_require_authentication(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        response = await client.request(method, path, json={"question": "x"})
        assert response.status_code == 401
