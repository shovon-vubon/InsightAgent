"""Knowledge-base tables: documents, their chunks, and per-model embeddings.

Three decisions here are load-bearing and worth stating at the top.

**Embeddings live in their own table, keyed by model** (plan D3). Two embedding
models can therefore index the same corpus at once, and the evaluation harness in
Phase 9 can compare them without re-ingesting a single file. A unique constraint
on `(chunk_id, embedding_model)` is what stops two vector spaces being silently
mixed inside one result set.

**The `embedding` column is an unconstrained `halfvec`.** pgvector allows mixed
dimensions in such a column and permits a *partial* HNSW index per model, built
over a dimension-casting expression (see the migration). The plan originally
assumed a fixed `halfvec(1536)` because it assumed OpenAI; that would have made a
768-dimension model impossible to store without padding. Half precision costs
almost nothing in recall at this corpus size and halves the index.

**`char_start`/`char_end` are relative to the document's cleaned text.** They are
what let the UI highlight the exact cited sentence rather than a whole chunk.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    Computed,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


DocumentStatusType = Enum(
    DocumentStatus,
    native_enum=False,
    length=16,
    validate_strings=True,
    name="document_status",
)


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_user_id_created_at", "user_id", "created_at"),
        Index("ix_documents_status", "status"),
        # Dedupe is per user, not global: two users uploading the same public PDF
        # each keep their own copy, and neither can probe for the other's files by
        # uploading a candidate and watching for a conflict.
        UniqueConstraint("user_id", "sha256", name="uq_documents_user_id_sha256"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    #: The name the user uploaded, sanitised. Never used to build a path.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Path on the artifact store, under a generated name (S4).
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        DocumentStatusType, nullable=False, default=DocumentStatus.UPLOADED
    )
    #: Populated on FAILED. User-safe text; the stack trace goes to the log.
    error: Mapped[str | None] = mapped_column(Text, default=None)

    title: Mapped[str | None] = mapped_column(String(512), default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Wall-clock cost of the whole pipeline, so slow formats are visible.
    ingestion_ms: Mapped[float | None] = mapped_column(default=None)

    user: Mapped[User] = relationship(back_populates="documents", lazy="raise")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.chunk_index",
        lazy="raise",
    )


class DocumentChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_document_id_chunk_index", "document_id", "chunk_index"),
        # GIN over the generated tsvector. Unused until Phase 4's lexical
        # retriever, but generating the column later would mean rewriting every
        # row of the table.
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Provenance. Nullable because not every format has pages.
    page_from: Mapped[int | None] = mapped_column(Integer, default=None)
    page_to: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Heading trail, e.g. "Q2 Review > Revenue > EMEA".
    section_path: Mapped[str | None] = mapped_column(String(512), default=None)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Maintained by PostgreSQL, so it cannot drift from `content`.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=False,
    )

    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")
    embeddings: Mapped[list[ChunkEmbedding]] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


#: Dimensions with a prebuilt HNSW index. See the migration for why the indexes
#: are keyed on dimension rather than on model name.
INDEXED_DIMENSIONS: tuple[int, ...] = (256, 384, 768, 1024, 1536, 3072)


def _hnsw_index(dimension: int) -> Index:
    """A partial HNSW index over the rows of one dimension.

    Declared on the model, not only in the migration, so that `alembic check` —
    the CI job that catches a model edited without a migration — compares like
    with like instead of reporting these as permanent drift.
    """
    return Index(
        f"ix_chunk_embeddings_hnsw_{dimension}",
        text(f"(embedding::halfvec({dimension})) halfvec_cosine_ops"),
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_where=text(f"embedding_dim = {dimension}"),
    )


class ChunkEmbedding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        # The invariant that keeps vector spaces from mixing.
        UniqueConstraint("chunk_id", "embedding_model", name="uq_chunk_embeddings_chunk_id_model"),
        Index("ix_chunk_embeddings_model_dim", "embedding_model", "embedding_dim"),
        *(_hnsw_index(dimension) for dimension in INDEXED_DIMENSIONS),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Stored explicitly rather than derived, so a mismatch is queryable.
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    #: No dimension modifier: see the module docstring.
    embedding: Mapped[Any] = mapped_column(HALFVEC(), nullable=False)

    chunk: Mapped[DocumentChunk] = relationship(back_populates="embeddings", lazy="raise")
