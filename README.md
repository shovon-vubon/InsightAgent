# InsightAgent

Autonomous AI research & data analysis agent — reasoning across documents, a relational
database, quantitative datasets, and external sources, with verified citations and
measured evaluation.

> **Status: Phase 0 — planning complete, no application code yet.**
> This README is a placeholder. The full README (architecture, results, setup) is
> written in Phase 13, and **no performance metric will appear here until the
> evaluation run that produced it exists in the database.**

## Current state

| Component | Status |
| --- | --- |
| Architecture & implementation plan | ✅ Complete — [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) |
| Backend | ⬜ Planned (Phase 1) |
| Frontend | ⬜ Planned (Phase 1) |
| LLM provider layer | ⬜ Planned (Phase 2) |
| Knowledge base / RAG | ⬜ Planned (Phases 3–4) |
| SQL agent | ⬜ Planned (Phase 5) |
| Analysis tools | ⬜ Planned (Phase 6) |
| Agent orchestration | ⬜ Planned (Phase 7) |
| Verification & confidence | ⬜ Planned (Phase 8) |
| Evaluation framework | ⬜ Planned (Phase 9) |
| Observability | ⬜ Planned (Phase 10) |

## Where to start

Read [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — it contains the
system architecture, database design, agent state machine, RAG pipeline, evaluation
design, security analysis, phase plan, and risk register.

## Note on data

All business data in this project is **synthetic**. "NovaRetail" is a fictional company
generated from a fixed seed; its financial reports are fictional documents generated from
that same data. Nothing here represents a real organisation.
