# Security

What is implemented today, what is deliberately deferred, and what is accepted
risk. Nothing here describes a control that does not exist — planned items are
labelled as such.

**This is a portfolio project running on synthetic data. It has not been
penetration tested. Do not put real data in it.**

---

## Implemented (Phase 1)

### Authentication

| Control | Implementation |
| --- | --- |
| Password hashing | argon2id via `argon2-cffi`, library defaults tracking OWASP guidance. `check_needs_rehash` supported for future parameter increases. |
| Password policy | Minimum 12 characters, maximum 128. Length over composition rules, per NIST SP 800-63B. The maximum bounds argon2 CPU cost per request. |
| Access tokens | HS256 JWT, 15-minute TTL. Algorithm pinned at decode, so `alg: none` and HMAC-size substitution are both rejected. `exp`, `iat`, `sub`, and `type` are required claims. |
| Refresh tokens | 256-bit opaque random values, stored as SHA-256 digests only, rotated on every use, delivered in an `HttpOnly` `SameSite=Lax` cookie scoped to `/api/v1/auth`, `Secure` in production. |
| Replay detection | Tokens are chained into families. Presenting a revoked token revokes every token in the family, and the revocation is committed before the request fails so a rollback cannot undo it. |
| User enumeration on login | An unknown email verifies against a dummy argon2 hash, so the timing and the response are indistinguishable from a wrong password. |

### Authorisation

- `USER` / `ADMIN` roles enforced at the service layer through FastAPI dependencies, not in route bodies.
- **Privilege is read from the database on every request, never from the token payload.** A token whose `role` claim says `ADMIN` grants nothing if the row says otherwise — there is a test asserting this.
- Deactivating an account takes effect immediately rather than at token expiry, because the user row is re-read per request.
- `OwnedRepository` makes the IDOR guard structural: user-owned tables get a repository that cannot build a query without an owner id. Cross-user reads return `None` rather than a distinct error, so existence stays unobservable.

### Data isolation

Application tables live in the `app` schema. The synthetic business data the
text-to-SQL agent will query (Phase 5) lives in `novaretail`, reachable only
through a dedicated `insight_ro` role that has:

- `SELECT` on `novaretail` and nothing else — **no grants in `app` at all**
- `default_transaction_read_only = on`
- `statement_timeout = 5s`, `idle_in_transaction_session_timeout = 10s`
- `search_path` fixed to `novaretail`

This is the primary containment boundary for the SQL agent, and it holds even if
every application-level check is bypassed. Verified manually:

```
$ psql -U insight_ro -c "SELECT count(*) FROM app.users;"
ERROR:  permission denied for schema app

$ psql -U insight_ro -c "CREATE TABLE novaretail.evil(x int);"
ERROR:  cannot execute CREATE TABLE in a read-only transaction
```

### Application hardening

- Settings refuse to start in production with `DEBUG=true`, the placeholder
  `SECRET_KEY`, wildcard CORS, or console logging. Tested.
- Error responses are sanitised envelopes carrying a `request_id`. Stack traces
  and exception text appear only outside production.
- Validation errors report field names and reasons but never echo the submitted
  value, so a mistyped password is not reflected back. Tested.
- Structured logging redacts any key matching `password|secret|token|api_key|
  authorization|cookie|credential|session_id` at any nesting depth. Tested.
- `X-Content-Type-Options`, `Referrer-Policy`, and `X-Frame-Options` set on all
  frontend responses.
- CORS origins come from configuration; `allow_credentials` is on, so `*` is
  rejected in production.
- Request IDs are truncated to 64 characters so a hostile header cannot bloat log
  records.
- Containers run as non-root users.
- `.env` is gitignored with an `!.env.example` negation; gitleaks runs in
  pre-commit and in CI.

---

## Accepted risks

### Registration confirms whether an email is registered

`POST /auth/register` returns 409 for an address that already exists. Avoiding
this requires an email-confirmation flow, which this project does not have.
**Login does not leak this** — only registration does.

### Client IP is taken from the socket, not `X-Forwarded-For`

Recorded on refresh tokens for the audit trail. Behind a proxy this records the
proxy's address. `X-Forwarded-For` is client-controlled and is deliberately not
trusted; enabling it requires running the ASGI server with proxy headers and a
trusted-hosts list.

### Access tokens cannot be revoked before expiry

Inherent to stateless JWTs. Bounded by the 15-minute TTL, and the per-request
user lookup means deactivation is still immediate in practice.

---

## Deferred to later phases

These are designed but **not implemented**. See `docs/IMPLEMENTATION_PLAN.md`.

| Control | Phase | Note |
| --- | --- | --- |
| SQL injection defence for text-to-SQL | 5 | `sqlglot` AST allowlist; the validated query is re-serialised from the AST and *that* is executed, so comment and statement-terminator tricks cannot survive. Forced `LIMIT`. Audit row per attempt. The `insight_ro` role above is already in place. |
| File upload validation | 3 | MIME sniffing rather than extension trust, size and quota limits, archive-bomb rejection, sanitised filenames, storage under generated names. |
| Prompt injection mitigation | 3, 7 | Retrieved content delimited and labelled untrusted; tool-routing decisions never taken from retrieved text; the SQL validator is independent of the model. **Mitigated, not solved** — this is a residual risk and will be stated as such in the README. |
| Sandboxed code execution | 12 | The default analysis path executes no model-authored code (ADR 0004, planned). Any free-form escape hatch gets a network-isolated, read-only, resource-capped container behind human approval. |
| Rate limiting and cost ceilings | 12 | Redis-backed per-user and per-endpoint limits; per-run token and spend caps. An unbounded agent loop against a paid API is a financial denial-of-service, so cost limits are treated as a security control. |
| SSRF protection for web research | 7 | Host resolution with private, loopback, and link-local ranges blocked; redirect, size, and time limits. |

---

## Reporting

This is a portfolio project, not a service. If you find a flaw, open an issue.
