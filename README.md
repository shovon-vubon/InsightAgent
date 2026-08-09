# InsightAgent

Autonomous AI research and data analysis agent — reasoning across documents, a
relational database, quantitative datasets, and external sources, with verified
citations and measured evaluation.

> **Status: Phase 1 complete — foundation.**
> The application runs, authenticates, and is fully containerised. **There is no AI
> functionality yet**; that begins in Phase 2. No performance metric will appear in
> this README until the evaluation run that produced it exists in the database.

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
| LLM provider abstraction & streaming chat | ⬜ Planned | 2 |
| Document ingestion, embeddings, citations | ⬜ Planned | 3 |
| Hybrid retrieval + reranking | ⬜ Planned | 4 |
| Text-to-SQL agent | ⬜ Planned | 5 |
| Typed Python analysis & charts | ⬜ Planned | 6 |
| Agent orchestration | ⬜ Planned | 7 |
| Verification, citations, confidence | ⬜ Planned | 8 |
| Evaluation framework & metrics | ⬜ Planned | 9 |
| Observability & cost tracking | ⬜ Planned | 10 |

The three feature pages in the UI (Research, Knowledge base, Datasets) render an
explicit *Not implemented* placeholder naming the phase that will build them. They
do not show mock data.

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

---

## Limitations

- **No AI functionality exists yet.** Phase 1 is infrastructure.
- Not penetration tested. Synthetic data only.
- Registration reveals whether an email is already registered; login does not.
  Explained in [`docs/SECURITY.md`](docs/SECURITY.md).
- Prompt injection will be *mitigated, not solved*, when retrieval lands.

## Note on data

All business data in this project is **synthetic**. "NovaRetail" is a fictional
company generated from a fixed seed, and its financial reports are fictional
documents generated from that same data. Nothing here represents a real
organisation.
