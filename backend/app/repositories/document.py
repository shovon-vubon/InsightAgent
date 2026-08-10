"""Document, chunk, and embedding access.

Built on `OwnedRepository`, so no query over documents can be constructed without
an owner id — the IDOR guard is structural rather than remembered (S5).

The dense search at the bottom is the only place in the application that writes
raw SQL. It is written out rather than composed through the ORM because the
`ORDER BY embedding <=> query` operator, the dimension cast that selects the
partial HNSW index, and the join back to chunks and documents all have to appear
in one statement for PostgreSQL to use the index at all. It is parameterised
throughout; the only interpolated value is an integer dimension that comes from
the embedding provider's own configuration, never from a request.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import InstrumentedAttribute

from app.models.document import ChunkEmbedding, Document, DocumentChunk, DocumentStatus
from app.repositories.base import OwnedRepository


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A search hit, carrying everything a citation needs."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    filename: str
    chunk_index: int
    content: str
    score: float
    page_from: int | None
    page_to: int | None
    section_path: str | None
    char_start: int
    char_end: int


class DocumentRepository(OwnedRepository[Document]):
    model = Document

    @classmethod
    def owner_column(cls) -> InstrumentedAttribute[Any]:
        return Document.user_id

    # --- documents ----------------------------------------------------------

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        stmt = self.scoped(user_id).order_by(Document.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_hash(self, user_id: uuid.UUID, sha256: str) -> Document | None:
        """The dedupe probe. Scoped to the owner, so it cannot confirm that
        another user holds a given file."""
        result = await self.session.execute(self.scoped(user_id).where(Document.sha256 == sha256))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        storage_path: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> Document:
        document = Document(
            user_id=user_id,
            filename=filename,
            storage_path=storage_path,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status=DocumentStatus.UPLOADED,
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def delete_document(self, document: Document) -> None:
        await self.session.delete(document)
        # Sessions run with autoflush off; without this a read later in the same
        # unit of work would still see the row.
        await self.session.flush()

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(Document.id)).where(Document.user_id == user_id)
        )
        return int(result.scalar_one())

    async def bytes_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
                Document.user_id == user_id
            )
        )
        return int(result.scalar_one())

    async def ready_document_ids(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self.session.execute(
            select(Document.id).where(
                Document.user_id == user_id, Document.status == DocumentStatus.READY
            )
        )
        return list(result.scalars().all())

    # --- chunks and embeddings ----------------------------------------------

    async def replace_chunks(
        self,
        document_id: uuid.UUID,
        rows: list[tuple[DocumentChunk, list[float], str, int]],
    ) -> None:
        """Write a document's chunks and their vectors, replacing any previous run.

        Re-ingestion is idempotent: the old chunks go first, so a document
        reprocessed with different chunking settings does not accumulate two
        generations of chunks that would both be retrievable.
        """
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

        for chunk, vector, model, dimensions in rows:
            self.session.add(chunk)
            await self.session.flush()
            self.session.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_model=model,
                    embedding_dim=dimensions,
                    embedding=vector,
                )
            )
        await self.session.flush()

    async def chunk_count(self, document_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
        )
        return int(result.scalar_one())

    # --- retrieval ----------------------------------------------------------

    def _scoped_chunk_query(self, user_id: uuid.UUID) -> Select[tuple[DocumentChunk]]:
        """Chunks belonging to this user's READY documents, and no others."""
        return (
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.user_id == user_id, Document.status == DocumentStatus.READY)
        )

    async def dense_search(
        self,
        *,
        user_id: uuid.UUID,
        query_vector: list[float],
        embedding_model: str,
        dimensions: int,
        limit: int,
        document_ids: list[uuid.UUID] | None = None,
    ) -> list[RetrievedChunk]:
        """Cosine nearest neighbours over this user's corpus.

        `1 - (a <=> b)` converts pgvector's cosine *distance* into a similarity in
        [0, 1], which is what the score floor and the UI are expressed in.
        """
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")

        # Interpolated because a type modifier cannot be a bind parameter. The
        # value is an int from the provider's own configuration and is asserted
        # above; it never originates from a request.
        cast_type = f"halfvec({int(dimensions)})"

        document_filter = ""
        params: dict[str, Any] = {
            "user_id": user_id,
            "query_vector": str(query_vector),
            "embedding_model": embedding_model,
            "dimensions": dimensions,
            "limit": limit,
        }
        if document_ids is not None:
            if not document_ids:
                return []
            document_filter = "AND d.id = ANY(:document_ids)"
            params["document_ids"] = document_ids

        # S608 is suppressed deliberately. The only interpolated values are
        # `cast_type`, built from an int that is bounds-checked above and comes
        # from the embedding provider's configuration, and `document_filter`,
        # which is one of two fixed literals. Every value from a request travels
        # as a bind parameter. A type modifier — `halfvec(768)` — cannot be a
        # bind parameter in PostgreSQL, which is why this query is textual at all.
        query = f"""
            SELECT
                c.id            AS chunk_id,
                d.id            AS document_id,
                COALESCE(d.title, d.filename) AS document_title,
                d.filename      AS filename,
                c.chunk_index   AS chunk_index,
                c.content       AS content,
                1 - (e.embedding::{cast_type} <=> (:query_vector)::{cast_type}) AS score,
                c.page_from     AS page_from,
                c.page_to       AS page_to,
                c.section_path  AS section_path,
                c.char_start    AS char_start,
                c.char_end      AS char_end
            FROM app.chunk_embeddings e
            JOIN app.document_chunks c ON c.id = e.chunk_id
            JOIN app.documents d       ON d.id = c.document_id
            WHERE d.user_id = :user_id
              AND d.status = 'READY'
              AND e.embedding_model = :embedding_model
              AND e.embedding_dim = :dimensions
              {document_filter}
            ORDER BY e.embedding::{cast_type} <=> (:query_vector)::{cast_type}
            LIMIT :limit
            """  # noqa: S608

        result = await self.session.execute(text(query), params)
        return [
            RetrievedChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_title=row.document_title,
                filename=row.filename,
                chunk_index=row.chunk_index,
                content=row.content,
                score=float(row.score),
                page_from=row.page_from,
                page_to=row.page_to,
                section_path=row.section_path,
                char_start=row.char_start,
                char_end=row.char_end,
            )
            for row in result
        ]
