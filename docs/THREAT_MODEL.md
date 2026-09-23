# Threat model

STRIDE walk-through of `backend/sentinel_api`, the only network-facing component. It is one thing:
a small operator tool exposing a defensive telemetry simulator, not a multi-tenant SaaS product,
and the mitigations below are scoped to that. Everything upstream of it (detection, attribution,
the agent, the simulator) is a pure library with no network surface of its own — see
[ARCHITECTURE.md](ARCHITECTURE.md) for how those pieces fit together.

## Assets

- **Incident evidence and reports**: what the platform decided and why. Confidentiality matters
  (it can describe a live security posture) but the bigger risk is *integrity* — a tampered
  incident or report is worse than a leaked one, because a leaked one is a demo dataset anyway.
- **The audit log**: the record of who did what. Its value is entirely in tamper-evidence.
- **User credentials**: password hashes and JWTs. Compromise here means account takeover.
- **The Anthropic API key**, if configured: a cost/abuse surface, not a data-confidentiality one
  (the agent's tools are read-only; nothing it can call leaks more than the incident already
  exposes through the REST API to the same caller).
- **Compute**: session creation and the investigation agent both cost real CPU time / API spend;
  unrestricted access is a denial-of-wallet and denial-of-service vector.

## STRIDE

The design choices behind the mitigations below are recorded in [ADR 0011](adr/0011-platform-security.md).

| Threat | Where | Mitigation | Residual risk |
|---|---|---|---|
| **Spoofing** identity | Any endpoint | JWT bearer tokens (HS256, 8 h expiry), password hashes are PBKDF2-HMAC-SHA256 with 600k iterations and a random salt (`security/passwords.py`) | No MFA, no session revocation list — a leaked token is valid until it expires. No password complexity policy is enforced server-side. |
| **Tampering** with incidents/reports | DB access, a compromised app process | Incidents and reports are written once by the app itself, never from client input; the audit log is hash-chained (`security/audit.py`) so a row edited after the fact breaks `GET /audit/verify` | The chain is application-enforced, not DB-enforced — a party with direct DB write access (not through the API) can edit a row *and* recompute every hash after it, hiding the tamper. True append-only would need a DB-level constraint or a separate write-only log store; not built. |
| **Tampering** with simulated telemetry/commands | The synthetic packet layer | CCSDS-like packets carry a truncated HMAC-SHA256 tag (ADR 0005); the key is a simulated key, not a real one | This defends the simulation's own internal consistency, not a real spacecraft — there is no real spacecraft in this system. |
| **Repudiation** | Login, session actions, investigations | Every login attempt (success or failure) and every session-create/control/investigate call is written to the audit log with the actor's email | Audit rows are keyed by the JWT's `sub` claim; a stolen token is indistinguishable from its rightful owner in the log. |
| **Information disclosure** | Ground truth via the API | Ground truth is withheld (`409`) until a session finishes (ADR already in `app.py`'s docstring), so a live "guess the cause" client can't peek | Once a session finishes, anyone with `viewer` access can read it — there is no per-incident access control, only the three global roles. |
| **Information disclosure** | Error responses | FastAPI's default handlers return structured 4xx bodies; no stack traces are returned to the client in a normal run | Uncaught exceptions in debug/dev mode could still reflect internals; production deployment should run behind `PUBLIC_DEMO_MODE=false` and a process manager that doesn't echo tracebacks. |
| **Denial of service** | Any endpoint, especially `POST /sessions` and `POST /incidents/{id}/investigate` | A per-IP in-memory sliding-window rate limiter (`security/ratelimit.py`); `MAX_CONCURRENT_SESSIONS` caps simultaneous scenario/replay runs; both mutating endpoints require `analyst` role even in demo mode | The rate limiter is per-process, in-memory — it does **not** work correctly across multiple worker processes or replicas (each keeps its own counters), so a real multi-worker deployment needs a shared store (Redis) instead. Not built; documented as a known gap. |
| **Elevation of privilege** | Role checks | Every mutating and admin route uses `require_role`, checked server-side per request against the JWT's `role` claim, not any client-supplied header | The role hierarchy is coarse (three roles, no per-resource ACL) — an `analyst` account can start sessions and run the agent against *any* incident, not just ones a given operator created. Acceptable for a single-operator tool; would need scoping for multi-tenant use. |

## What the AI investigation agent adds (see [ADR 0008](adr/0008-agent-hardening.md) and
[API.md](API.md#ai-investigation-agent))

The agent is the one component that ingests attacker-controllable free text (opcode names, auth
sources, detector explanations) and feeds it to an LLM. Its specific threat is prompt injection:
text embedded in telemetry trying to make the model misreport a verdict. Mitigations:

- Every tool is read-only and operates on a pre-fetched, frozen data bundle — a tool call cannot
  write, send a command, or reach the network, regardless of what the model decides to call.
- The system prompt tells the model that tool output is data, not instructions.
- The investigation can only end through `submit_report`, whose arguments are validated against a
  Pydantic schema; free text in the transcript never becomes the answer.
- Any failure — including "the model won't stop calling tools instead of submitting" — falls back
  to a deterministic, template-based report built directly from the evidence, so a successful
  injection at worst produces a wasted turn budget, not a false report reaching a person.

This is documented as **mitigated, not eliminated**: the live path is only tested against a stub
client (`tests/agent/test_client.py`), not a real model, because that costs money. The read-only
tool surface — not the prompt wording — is the actual safety boundary.

## Not covered by this threat model

- The (not yet built) Next.js frontend and its own attack surface (XSS, CSRF against the token
  storage strategy it picks, etc.) — out of scope until it exists.
- Supply-chain attacks on dependencies. Partially addressed by CI's `pip-audit`/`bandit`/Trivy jobs
  (all advisory, not yet a blocking gate — see `docs/PROGRESS.md`), Dependabot, and pinned lockfiles.
- Physical/host security, cloud IAM, TLS termination — this project ships an application, not
  infrastructure; a real deployment needs its own hardening for all of these.
