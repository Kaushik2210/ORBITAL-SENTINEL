# 11. Platform security: JWT auth, in-process rate limiting, an application-enforced audit chain

Date: 2026-09-23 · Status: accepted

## Context
The API (Phase 6) shipped unauthenticated, explicitly scoped to local use. Before it is safe to point a
frontend or a public demo at it, it needs identity, authorization, abuse protection and a record of who did
what. The project has no production deployment yet (single process, no Redis, no external identity provider),
so the honest choice is to build the smallest thing that is *correct for that shape* and document its limits,
rather than half-integrate infrastructure (Redis, OAuth) the project doesn't otherwise need.

## Decisions
1. **JWT (HS256), not sessions or an external IdP.** One shared secret (`JWT_SECRET`), three roles
   (`viewer < analyst < admin`) carried in the token, 8 h expiry, no refresh flow. Simplest thing that lets a
   stateless API check "who is this and what can they do" without a session store.
2. **PBKDF2-HMAC-SHA256 (600k iterations, stdlib `hashlib`) for password hashing, not bcrypt/argon2.** Avoids
   a new dependency for something this small; PBKDF2 at this iteration count is still an accepted choice per
   NIST SP 800-63B, even though argon2id is the modern first choice for new systems from scratch.
3. **No self-registration endpoint.** Accounts are provisioned with `scripts/tasks.py create-user`, writing
   directly to the `users` table. This is a single-operator tool, not a public sign-up product; an open
   registration endpoint would be pure attack surface with no corresponding benefit here.
4. **`PUBLIC_DEMO_MODE` relaxes only anonymous `GET` requests to `viewer`.** Every route that starts a
   session, controls one, calls the paid AI agent, or reads the audit log always requires a real token,
   regardless of demo mode. This lets the platform be browsed without an account while keeping every
   resource-costing or sensitive action behind a login.
5. **An in-memory, per-process, per-IP sliding-window rate limiter, not Redis.** Correct for the one
   deployment shape that exists today (a single `uvicorn` process); explicitly documented as insufficient for
   multiple workers or replicas, which would each keep independent counters. Swapping in a Redis-backed
   limiter later is a contained change (`security/ratelimit.py` is the only file that would move).
6. **A hash-chained, application-enforced audit log, not a DB-level append-only constraint.** Each row's hash
   commits to the previous row's hash plus its own fields (`security/audit.py`); `GET /audit/verify` walks the
   chain and reports the first row that no longer matches. This catches accidental or careless edits and makes
   a deleted/altered row detectable without every later hash also being fixed up. It does **not** protect
   against a party with direct database write access who edits a row *and* recomputes every hash after it —
   true tamper-resistance would need either a DB-level constraint (Postgres has no portable "insert-only"
   grant that SQLite also supports) or writing to an external append-only store, neither of which the project
   needs yet for a demo-scale audit trail.
7. **Security scanners (`bandit`, `pip-audit`) run in CI but are advisory (`continue-on-error: true`), not a
   merge gate.** A flaky vulnerability-DB network call or a low-severity finding on reviewed code (fixed-argv
   `subprocess.run`, unpickling the project's own generated artifact) shouldn't block unrelated work before
   there is a triage process for findings.

## Consequences
- No MFA, no token revocation list — a leaked token is valid until it expires (8 h). Acceptable for the
  current scale; would need addressing before handling real credentials or real spacecraft data.
- Three global roles, no per-resource ACL — any `analyst` can act on any incident. Fine for a single-operator
  tool; would need scoping for multi-tenant use.
- The rate limiter and the audit chain are both known to be insufficient beyond one process; this is stated
  in `docs/THREAT_MODEL.md` and `docs/LIMITATIONS.md`, not left implicit.
- `docs/THREAT_MODEL.md` is the STRIDE analysis this ADR's decisions are meant to satisfy; read them together.
