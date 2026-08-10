# ADR 0004 — LLM provider abstraction with a deterministic default

**Status:** Accepted (Phase 2)
**Date:** 2026-08-09

## Context

The system must work across OpenAI, Anthropic, and a local open-source model, and
must be testable in CI without spending money or depending on a third party's
uptime. Later phases add planning, routing, verification, and an LLM judge — all
of which multiply the number of calls.

## Decision

An abstract `LLMProvider` base class owning cross-cutting behaviour, with four
implementations: `OpenAIProvider`, `AnthropicProvider`, `OllamaProvider`, and
`FakeProvider`. Nothing above `app/llm/` imports a vendor SDK.

`FakeProvider` is the **default** in `.env.example`.

## Rationale

### Retry lives in the base class, not in each provider

Each provider implements only `_complete` and `_stream` and raises from a shared
error taxonomy. Timing, retry, backoff, and attempt counting are written once.
Adding a fifth provider means writing an API call and an exception mapping, not
re-deriving retry policy — and every provider's retry behaviour is identical and
tested through one suite.

### Retryable and non-retryable are distinguished explicitly

Rate limits, timeouts, and 5xx are retried; authentication failures and malformed
requests are not. Retrying a rejected credential only adds latency to a certain
failure.

### Streams retry only before the first byte

Once a delta has reached the client, replaying the call would duplicate visible
text. The stream wrapper tracks whether anything was emitted and re-raises rather
than retrying after that point. Tested.

### The test double is the default

Two reasons.

1. **CI must be free and deterministic.** A suite that calls a real model tests
   the model. Every agent-logic test from here on runs against `FakeProvider`.
2. **A fresh clone must run.** A reviewer can start the stack and use the app
   before obtaining any API key.

The obvious risk is someone mistaking canned text for model output, so it is
mitigated in four places: the response text says so, the `done` event carries
`is_test_double`, the UI shows a persistent banner, and **settings refuse to boot
in production while the provider is `fake`**.

### Unknown model prices record NULL, never an estimate

`cost_usd` is nullable, and the usage rollup reports `cost_coverage` alongside the
total. A guessed price would quietly become a fabricated "average cost per query"
in the README — precisely the kind of invented metric the project is meant to
avoid. Pricing entries carry `checked_on` and `source`.

### The chat service owns its own database sessions

A streaming response's body is produced after the endpoint returns, so writes made
mid-stream cannot rely on the request-scoped session's lifetime. The service opens
three short units of work per turn: persist the user message (before any model
call, so a provider failure cannot lose it), stream, then persist the reply and
the usage record. Failed calls are recorded too — hiding them would make the
reliability numbers meaningless.

## Consequences

- Providers cannot expose vendor-specific features without widening the interface. Accepted: structured output and tool calling will be added deliberately in Phases 5 and 7 rather than leaking through.
- OpenAI, Anthropic, and Ollama are **implemented but not yet verified against a live API** — no key or local model was available. Their error mapping and streaming are typed and reviewed but untested end to end, and the README says so.
- `FakeProvider`'s token counts are whitespace word counts, not real tokenisation. They exist to prove the accounting path works, never to bill anything.

## When to revisit

Provider fallback (Phase 12) needs a chain rather than a single instance; the
factory returns one provider today. Model routing by task complexity (brief §29)
sits at the same seam.
