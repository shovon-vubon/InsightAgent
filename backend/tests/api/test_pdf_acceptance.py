"""The Phase 3 acceptance gate, stated literally.

> Upload PDF, ask question, answer cites correct page.

Everything else in the suite tests a component. This tests the claim, on a real
multi-page PDF, through the HTTP API, ending at the page number in the citation.

The PDF is generated rather than committed: a binary fixture is opaque in review,
and generating it means the test states in code exactly which fact is on which
page — which is what the assertion then checks.
"""

from __future__ import annotations

import io
import uuid

import pytest
from httpx import AsyncClient
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from app.llm.fake import FakeProvider
from app.models.document import DocumentStatus
from app.services.ingestion import DocumentIngestionService
from tests.api.test_documents import register_and_login

#: (heading, body). The index in this list is the page the fact lands on.
PAGES: list[tuple[str, list[str]]] = [
    (
        "Revenue Overview",
        [
            "Total revenue for Q2 was 4.2 million dollars.",
            "This represents a decline of 12 percent compared with Q1.",
            "The decline was concentrated in the enterprise segment.",
        ],
    ),
    (
        "EMEA Regional Performance",
        [
            "EMEA revenue held flat at 1.1 million dollars.",
            "Mid-market churn rose to 4.1 percent in the region.",
            "This is the highest churn level recorded in two years.",
        ],
    ),
    (
        "Marketing Spend",
        [
            "Marketing spend totalled 380 thousand dollars.",
            "Paid search accounted for the entire reduction.",
            "Brand campaigns were unchanged from the prior quarter.",
        ],
    ),
]

#: The fact under test, and the 1-based page it is printed on.
EMEA_PAGE = 2


def build_pdf() -> bytes:
    """A three-page PDF with a larger heading per page, so headings are detectable."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=LETTER)
    _, height = LETTER

    for heading, lines in PAGES:
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(72, height - 90, heading)

        pdf.setFont("Helvetica", 11)
        y = height - 130
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return build_pdf()


class TestPhase3AcceptanceGate:
    async def test_upload_ask_and_cite_the_correct_page(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
        pdf_bytes: bytes,
    ) -> None:
        headers = await register_and_login(client, "pdf@example.com")

        # 1. Upload a real PDF.
        upload = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("q2-review.pdf", pdf_bytes, "application/pdf")},
        )
        assert upload.status_code == 201
        document_id = uuid.UUID(upload.json()["document_id"])

        # 2. Ingest it.
        await ingestion_service.process(document_id)
        detail = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
        assert detail["status"] == DocumentStatus.READY, detail["error"]
        assert detail["page_count"] == 3
        assert detail["chunk_count"] > 0

        # 3. Ask a question whose answer is only on page 2.
        fake_llm.script("EMEA revenue held flat at 1.1 million dollars [1].")
        response = await client.post(
            "/api/v1/documents/ask",
            headers=headers,
            json={"question": "What happened to EMEA revenue and mid-market churn?"},
        )
        body = response.json()

        # 4. The answer cites the correct page.
        assert response.status_code == 200
        assert body["insufficient_evidence"] is False
        assert body["invalid_markers"] == []
        assert body["citations"], "the answer cited nothing"

        citation = body["citations"][0]
        assert citation["page_from"] == EMEA_PAGE, (
            f"cited page {citation['page_from']}, expected {EMEA_PAGE}; "
            f"quote was: {citation['quote'][:200]}"
        )
        assert "EMEA" in citation["quote"]

    async def test_headings_become_section_paths(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        fake_llm: FakeProvider,
        pdf_bytes: bytes,
    ) -> None:
        """Font-size heading detection survives a round trip through a real PDF."""
        headers = await register_and_login(client, "sections@example.com")
        upload = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("q2.pdf", pdf_bytes, "application/pdf")},
        )
        await ingestion_service.process(uuid.UUID(upload.json()["document_id"]))

        fake_llm.script("Marketing spend totalled 380 thousand dollars [1].")
        body = (
            await client.post(
                "/api/v1/documents/ask",
                headers=headers,
                json={"question": "How much was marketing spend on paid search?"},
            )
        ).json()

        citation = body["citations"][0]
        assert citation["section_path"] is not None
        assert "Marketing" in citation["section_path"]

    async def test_title_is_taken_from_the_first_heading(
        self,
        client: AsyncClient,
        ingestion_service: DocumentIngestionService,
        pdf_bytes: bytes,
    ) -> None:
        headers = await register_and_login(client, "title@example.com")
        upload = await client.post(
            "/api/v1/documents",
            headers=headers,
            files={"file": ("q2.pdf", pdf_bytes, "application/pdf")},
        )
        document_id = uuid.UUID(upload.json()["document_id"])
        await ingestion_service.process(document_id)

        detail = (await client.get(f"/api/v1/documents/{document_id}", headers=headers)).json()
        assert detail["title"] == "Revenue Overview"
