# ADR 0005 — Embedding storage, dimension-agnostic indexing, and structural citations

**Status:** Accepted (Phase 3)
**Date:** 2026-08-10

## Context

Phase 3 delivers the vertical slice: upload a document, ask a question, get an
answer that cites the page the fact is on. Three decisions in it are load-bearing
enough to record, because each closes off a failure mode that would otherwise
surface much later as "the retrieval is bad".

Plan D3 already committed to embeddings living in their own table keyed by model,
so that the embedding model is an experiment variable the Phase 9 harness can
compare rather than a value baked into the corpus. Implementing it exposed a
detail the plan had not: the two candidate models produce **different numbers of
dimensions** (nomic-embed-text 768, text-embedding-3-small 1536), and the plan's
`halfvec(1536)` column cannot hold both.

## Decision

### 1. An unconstrained `halfvec` column, with partial HNSW indexes keyed by dimension

`chunk_embeddings.embedding` is declared `halfvec` with no dimension modifier.
pgvector permits mixed widths in such a column but cannot index one, so each
index fixes the width itself:

```sql
CREATE INDEX ix_chunk_embeddings_hnsw_768
    ON app.chunk_embeddings
    USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding_dim = 768;
```

Indexes are created for 256, 384, 768, 1024, 1536, and 3072 dimensions. They cost
nothing while empty, and only the one matching the configured model ever grows.

Keying on `embedding_dim` rather than on `embedding_model` means adopting a new
model of an existing width needs **no migration at all**. Queries filter on both:
`embedding_dim` to hit the index, `embedding_model` for correctness.

Half precision, not full: it halves index size at negligible recall cost for a
corpus of this size, and `halfvec` supports HNSW up to 4000 dimensions where
`vector` stops at 2000 — which is what makes `text-embedding-3-large` storable at
all.

### 2. Ollama `nomic-embed-text` as the default embedding provider

Independent of `LLM_PROVIDER`: the model that answers and the model that embeds
are separate variables and rarely the same vendor.

### 3. Citations are validated deterministically, before the answer is returned

Context assembly assigns each retrieved chunk a stable id, the prompt permits only
those ids, and a parser checks every emitted marker against the mapping. Unknown
markers are stripped and reported in `invalid_markers`.

## Rationale

### Why Ollama rather than OpenAI or sentence-transformers

| Option | Cost | Image size | Blocked on |
| --- | --- | --- | --- |
| Ollama `nomic-embed-text` | free | unchanged (HTTP) | an Ollama install |
| OpenAI `text-embedding-3-small` | ~pennies | unchanged | an API key |
| local sentence-transformers | free | **+2–3 GB** (torch) | nothing |

The third would trigger risk R1 (disk pressure on `C:`) a full phase early, and
Phase 9 re-embeds the corpus across configurations, so a per-run cost is a
recurring tax rather than a one-off. Ollama keeps torch out of the backend image
and the bill at zero. All three are implemented; only the default differs.

### Why the fake embedder is a hashing bag-of-words, not a random hash

Every retrieval test in the suite runs against the test double. A random-hash
embedder would make those tests tautologies: the pipeline would "work" while
ranking arbitrary chunks first, and a genuine ordering bug would pass. Hashing
tokens into buckets with sublinear scaling and L2 normalisation gives real
cosine structure, so `test_shared_vocabulary_scores_higher` is a meaningful
assertion and the retrieval assertions above it mean something.

It is lexical, not semantic. No number measured against it is a retrieval metric,
and `EMBEDDING_PROVIDER=fake` is refused in production.

### Why asymmetric embedding is handled inside the provider

`nomic-embed-text` is trained with `search_document:` and `search_query:`
prefixes, and omitting them costs real recall. The prefix is applied in the
provider rather than by callers, because a caller that forgets produces a corpus
that looks fine and retrieves badly — a failure with no error message.

### Why "no evidence" short-circuits before the model

If nothing clears the score floor, the service returns an explicit insufficiency
without calling the model. A pipeline that always calls the model always produces
prose, and prose always reads as an answer. This is the only mechanism by which
the system can be honest about the limits of its corpus, and it is free and fast
as a side effect.

### Why the answer endpoint does not stream

Citation validation runs over the *complete* answer — an unknown marker can only
be detected once the text containing it exists. Streaming would put an unverified
source in front of the user. Phase 7 streams agent *stages* instead, which
restores responsiveness without that trade.

## Consequences

- Changing `EMBEDDING_MODEL` after ingesting requires re-ingesting. Vectors are stored per model and a query only ever searches its own model's vectors, so the corpus does not become wrong — it becomes invisible. Documented in `.env.example`.
- The dense retriever is the only retriever. Lexical, RRF, and reranking are Phase 4, deliberately, so that Phase 4 has a measured baseline to prove itself against. The `tsvector` column and its GIN index are created now because generating them later would rewrite every row.
- Ollama and OpenAI embedding providers are **implemented but not yet executed against a live service** — the Ollama install failed on disk space (risk R1) and no API key is configured. Only the deterministic embedder has run end to end. The README says so.
- PDF heading detection is a font-size heuristic. A document that emphasises with weight rather than size yields no headings, and chunking degrades to packing paragraphs to the token budget. Stated rather than hidden.

## When to revisit

If a single corpus needs two models of the *same* dimension indexed
simultaneously, the partial indexes stop discriminating and should be keyed on
`(embedding_model, embedding_dim)` instead. If the corpus passes roughly a million
chunks, revisit D2 (pgvector over a dedicated vector store) rather than this ADR.
