"""The ingestion pipeline: bytes in, stored and embedded chunks out.

Deliberately a pure function of its inputs up to the point of persistence, so the
whole extract → clean → chunk → embed sequence is testable without a database:

    validate → extract → clean → chunk → embed → store → READY

Failure is a first-class outcome. A document that cannot be parsed is marked
`FAILED` with a message the user can act on, not retried forever and not silently
dropped. The distinction that matters is between a *transient* failure (the
embedding provider was down — worth retrying) and a *permanent* one (the PDF is a
scan with no text layer — retrying will never help), because the worker's retry
policy depends on it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.exceptions import DocumentProcessingError
from app.core.logging import get_logger
from app.llm.errors import LLMProviderError
from app.rag.embeddings.base import EmbeddingProvider, InputType
from app.rag.ingestion.chunking import Chunk, Chunker
from app.rag.ingestion.cleaning import clean
from app.rag.ingestion.extractors import extract
from app.rag.ingestion.validation import DocumentFormat

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    chunks: list[Chunk]
    vectors: list[list[float]]
    embedding_model: str
    embedding_dim: int
    title: str | None
    page_count: int | None
    full_text: str
    duration_ms: float

    def __post_init__(self) -> None:
        if len(self.chunks) != len(self.vectors):
            raise ValueError("Chunk and vector counts must match.")


class IngestionPipeline:
    def __init__(
        self,
        *,
        embedder: EmbeddingProvider,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self._embedder = embedder
        self._chunker = Chunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    async def run(self, payload: bytes, document_format: DocumentFormat) -> IngestionResult:
        started = time.perf_counter()

        extracted = clean(extract(payload, document_format))
        chunked = self._chunker.chunk(extracted)

        if not chunked.chunks:
            raise DocumentProcessingError("The document produced no indexable content.")

        try:
            result = await self._embedder.embed(
                [chunk.content for chunk in chunked.chunks],
                input_type=InputType.DOCUMENT,
            )
        except LLMProviderError as exc:
            # Re-raised as-is: the worker inspects the type to decide whether a
            # retry could ever succeed. Flattening it into
            # DocumentProcessingError here would lose that.
            logger.warning(
                "ingestion_embedding_failed",
                chunks=len(chunked.chunks),
                error_code=exc.error_code,
            )
            raise

        return IngestionResult(
            chunks=chunked.chunks,
            vectors=result.vectors,
            embedding_model=result.model,
            embedding_dim=result.dimensions,
            title=extracted.title,
            page_count=extracted.page_count,
            full_text=chunked.full_text,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
