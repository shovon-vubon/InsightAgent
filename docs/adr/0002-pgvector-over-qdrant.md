# ADR 0002 — pgvector inside PostgreSQL instead of a separate vector database

**Status:** Accepted (Phase 0)
**Date:** 2026-08-09

## Context

The RAG pipeline needs dense vector search combined with lexical search and
filtering by document metadata, over a corpus of roughly 5,000–20,000 chunks.
Qdrant was the alternative considered.

## Decision

Store embeddings in PostgreSQL using `pgvector`, in the same database as the
application schema. No separate vector service.

## Rationale

1. **Hybrid retrieval becomes one query.** Chunk text, metadata, the `tsvector`
   lexical index, and the embedding live in the same row, so dense and lexical
   retrieval fuse in a single SQL statement with a CTE per retriever. With a
   separate store, hybrid means two network round-trips plus an id-join back into
   PostgreSQL.

2. **No cross-store consistency problem.** Deleting or re-ingesting a document is
   one transaction. Two stores means orphaned vectors whenever the second write
   fails.

3. **Scale does not justify it.** pgvector's HNSW index handles this corpus with
   sub-10ms recall at k=50. Qdrant's advantages — distributed sharding, quantisation
   tiers, very large collections — begin far above where this project sits.

4. **One less service** on a machine whose OS drive has limited free space.

## Implementation notes

- `halfvec(1536)` with an HNSW index (`m=16`, `ef_construction=64`): halves index
  size at negligible recall cost at this scale.
- Embeddings live in `chunk_embeddings`, keyed by `(chunk_id, embedding_model)`
  rather than as a column on `document_chunks`. This makes the embedding model an
  experiment variable — two models can coexist over one corpus and be compared by
  the evaluation harness without re-ingesting documents — and makes "never mix
  vector spaces" a unique constraint rather than a convention.

## Consequences

- Ceiling around 10⁶ vectors before this decision needs revisiting.
- Fewer built-in ANN tuning knobs than a purpose-built engine.
- Vector index build time competes with OLTP load on the same instance. Acceptable
  at this size; would warrant a read replica or a dedicated store beyond it.

## When to revisit

Corpus beyond ~1M chunks, a need for distributed sharding, or retrieval latency
that profiling attributes to the index rather than to reranking.
