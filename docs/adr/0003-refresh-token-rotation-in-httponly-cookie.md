# ADR 0003 — Access token in memory, refresh token in an HttpOnly cookie

**Status:** Accepted (Phase 1)
**Date:** 2026-08-09

## Context

A single-page frontend needs to hold a credential across page loads. The usual
options are:

1. Both tokens in `localStorage` — simple, but readable by any XSS payload.
2. Both tokens in memory — XSS-resistant, but the session dies on every refresh.
3. Split: short-lived access token in memory, long-lived refresh token in an
   HttpOnly cookie.

Option 3 is standard, but it has a practical obstacle in development: with the
frontend on `localhost:3000` and the API on `localhost:8000`, the cookie is
cross-site, which requires `SameSite=None; Secure` — and `Secure` requires HTTPS,
which local development does not have.

## Decision

Option 3, with the cross-site problem removed rather than worked around: the
frontend proxies `/api/*` to the backend through a Next.js rewrite, so the browser
only ever talks to one origin.

- **Access token** — 15-minute JWT, returned in the response body, held in a
  module-level variable. Never persisted.
- **Refresh token** — 30-day opaque random string in a cookie marked `HttpOnly`,
  `SameSite=Lax`, `Path=/api/v1/auth`, and `Secure` in production.
- On page load the client calls `/auth/refresh` once to restore the session.

## Rationale

- **The proxy is the enabling decision.** Same-origin means `SameSite=Lax` works in
  development over plain HTTP *and* in production over HTTPS, with no environment
  branching in the cookie logic.
- **Path scoping** means the cookie is not attached to ordinary API calls, only to
  the four auth endpoints that need it.
- **Only the SHA-256 digest is stored.** The token is 256 bits of entropy, not a
  password, so a slow KDF buys nothing; a database leak yields no usable tokens.
- **Rotation with family revocation.** Each use issues a successor sharing a
  `family_id`. Presenting an already-revoked token means the value leaked, so the
  entire family is revoked. This is the main reason a rotating opaque token beats a
  second long-lived JWT: a JWT cannot be revoked.

## Consequences

- The legitimate user is signed out when replay is detected. That is intended:
  once a token from the chain has demonstrably leaked, the attacker's copy is
  indistinguishable from the user's.
- The client must serialise concurrent refreshes. Two parallel rotations would make
  the second replay a spent token and trip the detector, so `api-client.ts` shares
  one in-flight refresh promise. There is a test for this.
- A brief "Restoring session…" state on first paint while the silent refresh runs.
- Access tokens cannot be revoked before expiry. Mitigated by the 15-minute TTL and
  by re-reading the user row on every request, so deactivating an account takes
  effect immediately rather than at expiry.

## When to revisit

If a mobile or third-party client is added, cookies stop being the right transport
and this needs a token-based variant with its own revocation list.
