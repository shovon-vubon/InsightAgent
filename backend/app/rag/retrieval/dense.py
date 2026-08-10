"""Dense retrieval over pgvector.

Phase 3 deliberately ships **one** retriever. Lexical search, RRF fusion, and
cross-encoder reranking are Phase 4, and the point of separating them is that
Phase 4 has to *prove* each stage earns its latency against this baseline. A
hybrid pipeline shipped now, unmeasured, would make that ablation impossible —
there would be nothing to compare against.

The interface is therefore shaped for what comes next: `Retriever.retrieve`
returns scored evidence, and Phase 4 adds implementations behind the same call.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.core.logging import get_logger
from app.rag.embeddings.base import EmbeddingProvider
from app.repositories.document import DocumentRepository, RetrievedChunk

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    strategy: str
    #: How many candidates the store returned before filtering.
    candidates: int
    latency_ms: float
    embedding_model: str


class DenseRetriever:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        embedder: EmbeddingProvider,
        top_k: int = 8,
        score_floor: float = 0.25,
        #: Candidates fetched before the floor is applied. Over-fetching costs
        #: almost nothing on an HNSW index and stops the floor from emptying a
        #: result set that had good hits just outside `top_k`.
        overfetch: int = 3,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._top_k = top_k
        self._score_floor = score_floor
        self._overfetch = overfetch

    async def retrieve(
        self,
        query: str,
        *,
        user_id: uuid.UUID,
        document_ids: list[uuid.UUID] | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()

        # Embedded as a QUERY, not a DOCUMENT: asymmetric models prefix the two
        # differently, and using the wrong one measurably costs recall.
        query_vector = await self._embedder.embed_query(query)

        candidates = await self._repository.dense_search(
            user_id=user_id,
            query_vector=query_vector,
            embedding_model=self._embedder.default_model,
            dimensions=self._embedder.dimensions,
            limit=self._top_k * self._overfetch,
            document_ids=document_ids,
        )

        # The floor is what lets the system say "I have no evidence for this"
        # instead of confidently citing the least-irrelevant chunk it owns.
        kept = [chunk for chunk in candidates if chunk.score >= self._score_floor][: self._top_k]

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "retrieval_completed",
            strategy="dense",
            candidates=len(candidates),
            kept=len(kept),
            top_score=round(candidates[0].score, 4) if candidates else None,
            latency_ms=latency_ms,
        )

        return RetrievalResult(
            chunks=kept,
            strategy="dense",
            candidates=len(candidates),
            latency_ms=latency_ms,
            embedding_model=self._embedder.default_model,
        )
