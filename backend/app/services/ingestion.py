"""Upload handling and the ingestion job.

Split deliberately in two:

`DocumentIngestionService.accept` runs **inside the request**. It validates,
enforces quotas, stores the original, writes an `UPLOADED` row, and enqueues. It
does no parsing and no embedding, so the upload endpoint returns in milliseconds
regardless of whether the file is a one-page memo or a 400-page report.

`DocumentIngestionService.process` runs **on the worker**. It owns the state
machine `UPLOADED → PROCESSING → READY | FAILED`, and it is written to be safely
re-runnable: reprocessing an already-READY document replaces its chunks rather
than duplicating them.

The failure taxonomy is the part worth reading. A document that cannot be parsed
is *permanently* failed — retrying a scanned PDF a hundred times will not grow it
a text layer. An embedding provider being unreachable is *transient* and the job
is raised for arq to retry. Conflating the two either wastes a retry budget on
hopeless work or gives up on work that would have succeeded a minute later.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from arq.connections import ArqRedis

from app.core.config import Settings
from app.core.exceptions import (
    ConflictError,
    DocumentProcessingError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.session import Database
from app.llm.errors import LLMProviderError, is_retryable
from app.models.document import DocumentChunk, DocumentStatus
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.ingestion.pipeline import IngestionPipeline
from app.rag.ingestion.validation import CONTENT_TYPES, DocumentFormat, validate_upload
from app.rag.storage import DocumentStore
from app.repositories.document import DocumentRepository

logger = get_logger(__name__)

INGEST_TASK = "ingest_document"


@dataclass(frozen=True, slots=True)
class AcceptedUpload:
    document_id: uuid.UUID
    filename: str
    status: DocumentStatus
    size_bytes: int
    #: True when an identical file was already present and no new row was made.
    duplicate: bool


class TransientIngestionError(Exception):
    """Worth retrying. Raised so arq's retry policy can distinguish it."""


class DocumentIngestionService:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        store: DocumentStore,
        embedder: EmbeddingProvider,
        queue: ArqRedis | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._store = store
        self._embedder = embedder
        self._queue = queue

    # --- request path -------------------------------------------------------

    async def accept(self, *, user_id: uuid.UUID, payload: bytes, filename: str) -> AcceptedUpload:
        document_format, safe_name, digest = validate_upload(
            payload, filename, max_bytes=self._settings.MAX_UPLOAD_BYTES
        )

        async with self._database.session() as session:
            repository = DocumentRepository(session)

            existing = await repository.find_by_hash(user_id, digest)
            if existing is not None:
                # Content-addressed dedupe. Returning the existing document is
                # friendlier than a 409 and avoids a second copy of the bytes.
                return AcceptedUpload(
                    document_id=existing.id,
                    filename=existing.filename,
                    status=existing.status,
                    size_bytes=existing.size_bytes,
                    duplicate=True,
                )

            await self._enforce_quota(repository, user_id=user_id, incoming=len(payload))

            document = await repository.create(
                user_id=user_id,
                filename=safe_name,
                storage_path="",  # set below, once the id exists to name the file
                content_type=CONTENT_TYPES[document_format],
                size_bytes=len(payload),
                sha256=digest,
            )
            document.storage_path = await self._store.write(
                payload,
                user_id=user_id,
                document_id=document.id,
                document_format=document_format,
            )
            document_id = document.id

        await self._enqueue(document_id)
        logger.info(
            "document_accepted",
            document_id=str(document_id),
            format=document_format.value,
            size_bytes=len(payload),
        )
        return AcceptedUpload(
            document_id=document_id,
            filename=safe_name,
            status=DocumentStatus.UPLOADED,
            size_bytes=len(payload),
            duplicate=False,
        )

    async def _enforce_quota(
        self, repository: DocumentRepository, *, user_id: uuid.UUID, incoming: int
    ) -> None:
        """Per-user caps (S7). Cost control is treated as a security concern."""
        count = await repository.count_for_user(user_id)
        if count >= self._settings.MAX_DOCUMENTS_PER_USER:
            raise ConflictError(
                f"Document limit reached ({self._settings.MAX_DOCUMENTS_PER_USER}). "
                f"Delete a document before uploading another."
            )

        used = await repository.bytes_for_user(user_id)
        if used + incoming > self._settings.MAX_STORAGE_BYTES_PER_USER:
            limit_mb = self._settings.MAX_STORAGE_BYTES_PER_USER / (1024 * 1024)
            raise ConflictError(f"Storage quota of {limit_mb:.0f} MB would be exceeded.")

    async def _enqueue(self, document_id: uuid.UUID) -> None:
        if self._queue is None:
            # No worker configured. The document stays UPLOADED and visible
            # rather than silently never being processed.
            logger.warning("ingestion_queue_unavailable", document_id=str(document_id))
            return
        await self._queue.enqueue_job(INGEST_TASK, str(document_id))

    async def delete(self, *, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        async with self._database.session() as session:
            repository = DocumentRepository(session)
            document = await repository.get_owned(document_id, user_id)
            if document is None:
                raise NotFoundError("Document not found.")
            storage_path = document.storage_path
            # Chunks and embeddings go with it via ON DELETE CASCADE.
            await repository.delete_document(document)

        # After the transaction commits: a failed unlink must not roll back a
        # delete the user has been told succeeded.
        if storage_path:
            await self._store.delete(storage_path)
        logger.info("document_deleted", document_id=str(document_id))

    # --- worker path --------------------------------------------------------

    async def process(self, document_id: uuid.UUID) -> None:
        """Extract, chunk, embed, and store. Owns the document's status."""
        started = time.perf_counter()

        async with self._database.session() as session:
            document = await DocumentRepository(session).get_by_id(document_id)
            if document is None:
                logger.warning("ingestion_document_missing", document_id=str(document_id))
                return
            document.status = DocumentStatus.PROCESSING
            document.error = None
            storage_path = document.storage_path
            content_type = document.content_type
            user_id = document.user_id

        try:
            payload = await self._store.read(storage_path)
            document_format = _format_from_content_type(content_type)
            pipeline = IngestionPipeline(
                embedder=self._embedder,
                chunk_size=self._settings.CHUNK_SIZE_TOKENS,
                chunk_overlap=self._settings.CHUNK_OVERLAP_TOKENS,
            )
            result = await pipeline.run(payload, document_format)
        except LLMProviderError as exc:
            # The embedding provider failed. Retryable ones go back on the queue;
            # a bad credential or an unpulled model will never succeed and is
            # recorded as failed so the user sees something actionable.
            if is_retryable(exc):
                await self._mark_retrying(document_id, exc.message)
                raise TransientIngestionError(exc.message) from exc
            await self._mark_failed(document_id, exc.message)
            return
        except (DocumentProcessingError, ValidationError) as exc:
            await self._mark_failed(document_id, exc.message)
            return
        except Exception:
            logger.exception("ingestion_unexpected_failure", document_id=str(document_id))
            await self._mark_failed(
                document_id, "The document could not be processed due to an internal error."
            )
            return

        async with self._database.session() as session:
            repository = DocumentRepository(session)
            document = await repository.get_by_id(document_id)
            if document is None:  # deleted while we were working
                logger.info("ingestion_document_deleted_midway", document_id=str(document_id))
                return

            await repository.replace_chunks(
                document_id,
                [
                    (
                        DocumentChunk(
                            document_id=document_id,
                            chunk_index=chunk.index,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            page_from=chunk.page_from,
                            page_to=chunk.page_to,
                            section_path=chunk.section_path,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            chunk_metadata={},
                        ),
                        vector,
                        result.embedding_model,
                        result.embedding_dim,
                    )
                    for chunk, vector in zip(result.chunks, result.vectors, strict=True)
                ],
            )

            document.status = DocumentStatus.READY
            document.error = None
            document.title = result.title or document.filename
            document.page_count = result.page_count
            document.chunk_count = len(result.chunks)
            document.ingestion_ms = round((time.perf_counter() - started) * 1000, 2)

        logger.info(
            "document_ingested",
            document_id=str(document_id),
            user_id=str(user_id),
            chunks=len(result.chunks),
            embedding_model=result.embedding_model,
            duration_ms=result.duration_ms,
        )

    async def _mark_failed(self, document_id: uuid.UUID, message: str) -> None:
        async with self._database.session() as session:
            document = await DocumentRepository(session).get_by_id(document_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
                document.error = message[:500]
        logger.warning("document_ingestion_failed", document_id=str(document_id), reason=message)

    async def _mark_retrying(self, document_id: uuid.UUID, message: str) -> None:
        """Keep the document in PROCESSING but surface why it is taking a while."""
        async with self._database.session() as session:
            document = await DocumentRepository(session).get_by_id(document_id)
            if document is not None:
                document.status = DocumentStatus.PROCESSING
                document.error = f"Retrying: {message}"[:500]


_CONTENT_TYPE_TO_FORMAT = {value: key for key, value in CONTENT_TYPES.items()}


def _format_from_content_type(content_type: str) -> DocumentFormat:
    document_format = _CONTENT_TYPE_TO_FORMAT.get(content_type)
    if document_format is None:  # pragma: no cover - only a corrupted row reaches this
        raise DocumentProcessingError(f"Unsupported stored content type: {content_type}")
    return document_format
