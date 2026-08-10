from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.document import DocumentStatus
from app.services.knowledge import MAX_QUESTION_LENGTH


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    title: str | None
    content_type: str
    size_bytes: int
    status: DocumentStatus
    #: User-safe reason the document failed. `None` unless status is FAILED.
    error: str | None
    page_count: int | None
    chunk_count: int
    ingestion_ms: float | None
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    filename: str
    status: DocumentStatus
    size_bytes: int
    #: True when an identical file was already in the knowledge base, in which
    #: case `document_id` refers to the existing document.
    duplicate: bool


class CitationRead(BaseModel):
    """A validated citation. Every field traces back to a stored chunk."""

    marker: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    filename: str
    #: The chunk text the claim was drawn from.
    quote: str
    #: Cosine similarity to the question, in [0, 1].
    score: float
    page_from: int | None
    page_to: int | None
    section_path: str | None
    #: Offsets into the document's cleaned text, for highlighting the exact span.
    char_start: int
    char_end: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    #: Restrict retrieval to these documents. Omit to search the whole corpus.
    document_ids: list[uuid.UUID] | None = None

    @field_validator("question")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The question must not be blank")
        return value


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationRead]
    #: True when nothing cleared the retrieval score floor. The answer then says
    #: so explicitly and no model was called.
    insufficient_evidence: bool
    #: Citation ids the model emitted that matched no supplied source. Non-empty
    #: means the model fabricated a reference; it was stripped from `answer`.
    invalid_markers: list[int]
    candidates_considered: int
    retrieval_ms: float
    total_ms: float
    provider: str
    model: str
    is_test_double: bool
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


class KnowledgeStats(BaseModel):
    """Corpus summary for the knowledge base page."""

    documents: int
    ready: int
    processing: int
    failed: int
    total_chunks: int
    total_bytes: int
    storage_limit_bytes: int
    document_limit: int
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    is_test_double: bool
