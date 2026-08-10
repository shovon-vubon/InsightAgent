# InsightAgent

Autonomous AI research and data analysis agent — reasoning across documents, a
relational database, quantitative datasets, and external sources, with verified
citations and measured evaluation.

> **Status: Phase 3 complete — knowledge base with cited answers.**
> The application runs, authenticates, streams model responses, ingests uploaded
> documents in the background, and answers questions from them with citations that
> point at a specific page. **There is no SQL, tool use, or agent orchestration
> yet** — that starts in Phase 5, and the assistant is prompted to say so rather
> than guess. No performance metric will appear in this README until the
> evaluation run that produced it exists in the database.

---

## Current state

Honest labels, per phase. See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
for the full plan.

| Component | Status | Phase |
| --- | --- | --- |
| Architecture & implementation plan | ✅ Complete | 0 |
| Backend service (FastAPI, async SQLAlchemy) | ✅ Implemented | 1 |
| PostgreSQL + pgvector, Redis, Alembic migrations | ✅ Implemented | 1 |
| Authentication (argon2id, JWT, rotating refresh tokens) | ✅ Implemented | 1 |
| Docker Compose stack | ✅ Implemented | 1 |
| CI (lint, types, tests, migrations, images, secret scan) | ✅ Implemented | 1 |
| Frontend shell (Next.js, login, authed layout) | ✅ Implemented | 1 |
| LLM provider abstraction (OpenAI, Anthropic, Ollama, Fake) | ⚠️ Implemented, only Fake verified live | 2 |
| Streaming chat over SSE + conversation persistence | ✅ Implemented | 2 |
| Token, latency, and cost tracking per call | ✅ Implemented | 2 |
| Versioned prompt registry | ✅ Implemented | 2 |
| Document ingestion (PDF/DOCX/XLSX/CSV/TXT/MD) on a background worker | ✅ Implemented | 3 |
| Embedding abstraction (Ollama, OpenAI, Fake) + Redis cache | ⚠️ Implemented, only Fake verified live | 3 |
| Dense retrieval over pgvector, with a score floor | ✅ Implemented | 3 |
| Cited answers with deterministic citation validation | ✅ Implemented | 3 |
| Knowledge base UI (upload, status, sources) | ✅ Implemented | 3 |
| Hybrid retrieval (lexical + RRF) + reranking | ⬜ Planned | 4 |
| Text-to-SQL agent | ⬜ Planned | 5 |
| Typed Python analysis & charts | ⬜ Planned | 6 |
| Agent orchestration | ⬜ Planned | 7 |
| Verification, citations, confidence | ⬜ Planned | 8 |
| Evaluation framework & metrics | ⬜ Planned | 9 |
| Observability & cost tracking | ⬜ Planned | 10 |

Research and Knowledge base are live. Datasets still renders an explicit
*Not implemented* placeholder naming the phase that will build it. No page shows
mock data.

---

## What Phase 1 actually delivers

**Backend**

- FastAPI app factory with lifespan-managed database and Redis pools
- Pydantic settings that refuse to boot in production with a placeholder secret,
  `DEBUG=true`, wildcard CORS, or non-JSON logging
- structlog with request-id correlation and secret redaction at any nesting depth
- Application exception hierarchy mapped to sanitised error envelopes carrying a
  `request_id`; stack traces never escape in production
- Async SQLAlchemy 2.0 in a dedicated `app` schema; Alembic configured for async,
  with the initial migration verified reversible against a live database
- Liveness and readiness probes split so a database blip does not trigger a
  restart loop

**Security** — detail in [`docs/SECURITY.md`](docs/SECURITY.md)

- argon2id passwords; 15-minute access JWTs with the algorithm pinned at decode
- Refresh tokens: opaque, stored only as SHA-256 digests, rotated on every use,
  chained into families so a replayed token revokes the whole chain
- Role read from the database on every request — a forged `ADMIN` claim grants
  nothing
- `OwnedRepository` makes the IDOR guard structural: no query without an owner id
- A `SELECT`-only `insight_ro` PostgreSQL role with **no grants in the `app`
  schema**, ready to contain the Phase 5 text-to-SQL agent

**Frontend**

- Next.js App Router, TypeScript strict, Tailwind v4
- API client with a single shared in-flight refresh, so concurrent 401s cannot
  race into a false replay-detection logout
- Login page, authenticated shell with sidebar, client-side session restore

**Verification**

| Gate | Result |
| --- | --- |
| Backend tests | 66 passed |
| Frontend tests | 12 passed |
| `ruff check` + `ruff format --check` | clean |
| `mypy --strict` (app and tests) | clean |
| `alembic upgrade head` → `downgrade base` → `upgrade head` | reversible |
| `alembic check` | no un-migrated model drift |

---

## What Phase 3 actually delivers

The vertical slice: **upload a PDF, ask a question, get an answer that cites the
page the fact is on.**

**Ingestion** — uploads return in milliseconds and are processed by an `arq`
worker, so response time does not depend on document size. Format is decided by
**content, not filename**: an executable renamed `report.pdf` is rejected, as is
an `.xlsx` renamed `.docx`, and OOXML archives are checked for decompression
bombs. Files are stored under generated names derived from the document's UUID,
so no client-supplied string ever reaches the filesystem.

Extraction normalises PDF, DOCX, XLSX, CSV, TXT, and Markdown into one `Block`
list carrying page numbers and heading levels, so chunking — and therefore
citation precision — is format-independent. Cleaning repairs line-break
hyphenation and removes headers and footers detected by *recurrence across pages*
rather than by position.

**Chunking is structure-aware**: sections split first, blocks pack to a token
budget, oversized blocks split between sentences, and overlap carries whole
sentences — never across a section boundary, which would file one section's facts
under another's heading. Every chunk records its section path, page range, and
character offsets into the cleaned text.

**Retrieval and citations** — cosine nearest neighbours over pgvector with a
score floor. If nothing clears it, the system says the documents do not cover the
question **and does not call the model at all**. Otherwise each chunk gets a
stable id, the prompt permits only those ids, and a deterministic parser checks
every marker the model emitted. A fabricated `[99]` is stripped and reported;
it cannot reach the user.

**Verification**

| Gate | Result |
| --- | --- |
| Backend tests | 250 passed |
| Frontend tests | 29 passed |
| `ruff check` + `ruff format --check` | clean |
| `mypy --strict` (app and tests) | clean |
| Migration `43cb018a3a35` up → down → up | reversible |
| `alembic check` | no un-migrated model drift |
| Acceptance gate | a generated 3-page PDF, uploaded over HTTP, answers a page-2 question with `page_from == 2` |

---

## Local setup

### Prerequisites

- Docker Desktop with Compose v2
- [uv](https://docs.astral.sh/uv/) (backend) and Node 24+ (frontend), for running
  outside containers

> **Disk note:** Docker's WSL2 disk image lives on `C:` by default. If your OS
> drive is tight, move it first — Settings → Resources → Advanced → Disk image
> location. Moving the repository elsewhere does not move the images.

### Run the whole stack

```bash
cp .env.example .env
# generate a key and paste it into SECRET_KEY:
python -c "import secrets; print(secrets.token_hex(32))"

docker compose up -d --build
```

- Frontend: <http://localhost:3000>
- API docs: <http://localhost:8000/api/v1/docs>
- Readiness: <http://localhost:8000/api/v1/health/ready>

The backend applies migrations on start. Create the bootstrap admin with
`make seed`, or register a normal account from the login page.

### Run natively (faster inner loop)

```bash
docker compose up -d postgres redis     # or: make infra

cd backend && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --reload

cd frontend && npm install && npm run dev
```

`.env` points `DATABASE_URL` at `localhost`; Compose overrides it to the
`postgres` service name, so one file works for both.

### Common commands

`make help` lists everything. Without `make`:

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run mypy app tests
cd frontend && npm run test && npm run typecheck && npm run build
```

---

## Testing

Backend tests run against a **real** PostgreSQL and Redis. The suite drops and
recreates a throwaway database, migrates it with the actual
`alembic upgrade head`, then runs each test inside a transaction that is rolled
back — so a model that has drifted from its migration fails the suite instead of
passing against a schema nobody ships.

Requires `make infra` (or CI service containers) to be running. If PostgreSQL is
unreachable the integration tests skip with a clear message rather than failing
confusingly.

---

## Environment variables

Every variable is documented in [`.env.example`](.env.example). `.env` is
gitignored, gitleaks runs in pre-commit and CI, and the application fails fast on
a missing or placeholder secret rather than starting with a predictable key.

---

## Repository layout

```
backend/    FastAPI service — api, core, db, models, repositories, services, migrations, tests
frontend/   Next.js App Router — app, components, features, services, types, tests
infra/      PostgreSQL bootstrap (extensions, schemas, read-only role)
docs/       Implementation plan, security notes, architecture decision records
```

---

## Architecture decisions

| ADR | Decision |
| --- | --- |
| [0001](docs/adr/0001-custom-orchestrator-over-langgraph.md) | Custom state machine instead of LangGraph |
| [0002](docs/adr/0002-pgvector-over-qdrant.md) | pgvector in PostgreSQL instead of a separate vector database |
| [0003](docs/adr/0003-refresh-token-rotation-in-httponly-cookie.md) | Access token in memory, refresh token in an HttpOnly cookie |
| [0004](docs/adr/0004-llm-provider-abstraction.md) | LLM provider abstraction with a deterministic default |
| [0005](docs/adr/0005-embedding-storage-and-retrieval.md) | Dimension-agnostic embedding storage and structural citation validation |

---

## Limitations

- **No SQL, tool use, or agent orchestration exists yet.** Phases 5–7. The chat system prompt tells the model to say it cannot answer from evidence rather than guess.
- **No real embedding or language model has been run end to end.** The OpenAI, Anthropic, and Ollama providers — for both chat and embeddings — are implemented, typed, and reviewed but have never been executed against a live service: no API key is configured, and the Ollama install failed for lack of disk space. Only the deterministic doubles have run. Treat everything else as unverified.
- **No retrieval quality number exists, and none may be quoted.** The deterministic embedder is a hashing bag-of-words: it has real lexical structure, which is what makes the retrieval tests meaningful, but it has no semantics. Measuring recall against it would be measuring string overlap. Phase 4 produces the ablation table, and needs a real embedding model first.
- **Retrieval is dense-only.** Lexical search, RRF fusion, and cross-encoder reranking are Phase 4, held back deliberately so that Phase 4 has a measured baseline to prove itself against.
- **PDF heading detection is a font-size heuristic.** A document that emphasises with weight rather than size yields no headings, and chunking falls back to packing paragraphs to the token budget. Citations then carry a page but no section path.
- Spreadsheets in the knowledge base are treated as documents to read, not datasets to query. Structured analysis over tabular data is Phase 6.
- `FakeProvider`'s token counts are whitespace word counts, not real tokenisation. They exercise the accounting path; they are not billing figures.
- Not penetration tested. Synthetic data only.
- Registration reveals whether an email is already registered; login does not.
  Explained in [`docs/SECURITY.md`](docs/SECURITY.md).
- **Prompt injection is mitigated, not solved.** Retrieved document text is delimited and labelled untrusted in the prompt, and the citation validator is independent of the model — but a sufficiently persuasive uploaded document can still influence an answer's wording. Tool-calling decisions are not taken from retrieved text; that boundary matters more from Phase 7 onward.

## Note on data

All business data in this project is **synthetic**. "NovaRetail" is a fictional
company generated from a fixed seed, and its financial reports are fictional
documents generated from that same data. Nothing here represents a real
organisation.
