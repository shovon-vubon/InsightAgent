# ADR 0001 — Custom state machine instead of LangGraph

**Status:** Accepted (Phase 0)
**Date:** 2026-08-09

## Context

The agent runtime needs planning, tool routing, a bounded execution loop, human
approval pauses, step-level observability, and resumability. LangGraph provides
graph topology, checkpointing, and interrupt-based human-in-the-loop. The brief
permits either LangGraph or a clean custom state machine.

## Decision

Write the orchestrator by hand. LangGraph, LangChain, and LlamaIndex are excluded
from the dependency set.

## Rationale

1. **The topology is fixed, not dynamic.** `plan → route → execute → aggregate →
   verify → synthesise`, with one bounded loop between `route` and `execute`. That
   is roughly 300 lines of explicit Python, not a graph engine's problem.

2. **The checkpointer already has to exist.** Brief §26 and §36 require every agent
   step to be persisted for the trace viewer. Once `agent_steps` stores a state
   snapshot per transition, checkpointing and resume-from-`PENDING_APPROVAL` fall
   out of it. LangGraph would add a *second*, redundant checkpoint store with its
   own consistency questions.

3. **Testability.** A hand-written loop is directly unit-testable with a
   `FakeProvider` and stub tools, with no framework lifecycle in the way.

4. **Stability.** LangGraph's API has moved substantially across minor versions.
   A portfolio project that must still run in a year should not carry that.

5. **It is the thing being demonstrated.** Wiring a framework shows less than
   building the loop, and the loop is the point of the project.

## Consequences

**Accepted cost:** we hand-write retry, timeout, budget enforcement, duplicate-call
detection, and step persistence — an estimated two extra days in Phase 7.

**Boundaries kept clean anyway:** tools, LLM providers, and prompts live behind
their own interfaces, so the orchestrator is replaceable without touching them.

## When to revisit

- The graph acquires genuine parallel fan-out with conditional joins.
- Durable execution across process restarts becomes a requirement.
- Multi-agent delegation, where per-agent isolation and message passing would
  otherwise be reinvented.
