"""Knowledge base: upload, status, deletion, and cited question answering."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status

from app.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    EmbeddingProviderDep,
    IngestionServiceDep,
    KnowledgeServiceDep,
)
from app.api.route import CommittingRoute
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.document import DocumentStatus
from app.repositories.document import DocumentRepository
from app.schemas.document import (
    AskRequest,
    AskResponse,
    CitationRead,
    DocumentRead,
    KnowledgeStats,
    UploadResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["knowledge base"], route_class=CommittingRoute)


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    user: CurrentUser,
    service: IngestionServiceDep,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="PDF, DOCX, XLSX, CSV, TXT, or Markdown")],
) -> UploadResponse:
    """Accept a file, store it, and queue it for ingestion.

    Returns as soon as the bytes are safely on disk. Parsing and embedding happen
    on the worker, so the response time does not depend on document size — poll
    `GET /documents/{id}` for the status transition to READY.
    """
    if not file.filename:
        raise ValidationError("The upload must include a filename.")

    # Read with a hard ceiling rather than trusting the declared size: an
    # attacker controls Content-Length, and `UploadFile` will happily spool a
    # multi-gigabyte body to disk before any handler sees it.
    payload = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(payload) > settings.MAX_UPLOAD_BYTES:
        limit_mb = settings.MAX_UPLOAD_BYTES / (1024 * 1024)
        raise ValidationError(f"File exceeds the {limit_mb:.0f} MB upload limit.")

    accepted = await service.accept(user_id=user.id, payload=payload, filename=file.filename)
    return UploadResponse(
        document_id=accepted.document_id,
        filename=accepted.filename,
        status=accepted.status,
        size_bytes=accepted.size_bytes,
        duplicate=accepted.duplicate,
    )


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentRead]:
    documents = await DocumentRepository(session).list_for_user(user.id, limit=limit, offset=offset)
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/stats", response_model=KnowledgeStats)
async def knowledge_stats(
    session: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    embedder: EmbeddingProviderDep,
) -> KnowledgeStats:
    """Corpus summary, including which embedding model the vectors were built with."""
    repository = DocumentRepository(session)
    documents = await repository.list_for_user(user.id, limit=1000)

    return KnowledgeStats(
        documents=len(documents),
        ready=sum(1 for d in documents if d.status is DocumentStatus.READY),
        processing=sum(
            1 for d in documents if d.status in (DocumentStatus.PROCESSING, DocumentStatus.UPLOADED)
        ),
        failed=sum(1 for d in documents if d.status is DocumentStatus.FAILED),
        total_chunks=sum(d.chunk_count for d in documents),
        total_bytes=sum(d.size_bytes for d in documents),
        storage_limit_bytes=settings.MAX_STORAGE_BYTES_PER_USER,
        document_limit=settings.MAX_DOCUMENTS_PER_USER,
        embedding_provider=embedder.provider_label,
        embedding_model=embedder.default_model,
        embedding_dimensions=embedder.dimensions,
        is_test_double=embedder.provider_label == "fake",
    )


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> DocumentRead:
    document = await DocumentRepository(session).get_owned(document_id, user.id)
    if document is None:
        # Same answer whether it does not exist or belongs to someone else.
        raise NotFoundError("Document not found.")
    return DocumentRead.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, service: IngestionServiceDep
) -> None:
    await service.delete(user_id=user.id, document_id=document_id)


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest, user: CurrentUser, service: KnowledgeServiceDep) -> AskResponse:
    """Answer a question from the user's own documents, with validated citations.

    Not streamed. The citation validation in `app.rag.citations` runs over the
    *complete* answer — an unknown marker can only be detected once the text
    containing it exists — and streaming a claim before its citation has been
    checked would put an unverified source in front of the user. Phase 7 streams
    the agent's *stages* instead, which carries the same responsiveness without
    that trade.
    """
    result = await service.ask(
        user_id=user.id, question=payload.question, document_ids=payload.document_ids
    )
    return AskResponse(
        answer=result.answer,
        citations=[
            CitationRead(
                marker=citation.marker,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                document_title=citation.document_title,
                filename=citation.filename,
                quote=citation.quote,
                score=citation.score,
                page_from=citation.page_from,
                page_to=citation.page_to,
                section_path=citation.section_path,
                char_start=citation.char_start,
                char_end=citation.char_end,
            )
            for citation in result.citations
        ],
        insufficient_evidence=result.insufficient_evidence,
        invalid_markers=result.invalid_markers,
        candidates_considered=result.candidates_considered,
        retrieval_ms=result.retrieval_ms,
        total_ms=result.total_ms,
        provider=result.provider,
        model=result.model,
        is_test_double=result.is_test_double,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )
