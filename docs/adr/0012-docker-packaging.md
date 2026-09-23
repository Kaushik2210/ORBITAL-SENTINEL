# 12. Docker packaging: a lean API image, and CI as the only place it actually runs

Date: 2026-09-23 · Status: accepted

## Context
The platform needed a one-command demo (`docker compose up`). Two decisions had to be made honestly: what
goes into the API image, given `ml` (torch, onnxruntime) and `agent` (anthropic) are optional extras that
together roughly double dependency size; and how to verify any of this works at all, given the development
machine has no Docker installed.

## Decisions
1. **The API image installs neither the `ml` nor the `agent` extra.** Both paths already degrade gracefully
   without their dependency: `AppState.replay_engine` only builds a `ForecasterDetector` when
   `L2_MODELS_DIR` actually contains `.onnx` files (state.py), and `sentinel_agent.client.investigate` falls
   back to the deterministic offline report whenever the `anthropic` package or a key isn't available
   (already true since Phase 8, not new behavior for Docker). A demo image that runs L1/L3-L5 detection,
   attribution, and the offline agent report out of the box, without a multi-gigabyte torch layer, is a better
   default than a heavier image nobody asked for. A deployment that wants live L2 or the live agent adds the
   extra in its own build — this is stated directly in `backend/Dockerfile`, not left implicit.
2. **Neither real SMAP/MSL data nor trained L2 models are baked into the image.** Consistent with them never
   being committed to the repo either (ADR 0003's fallback chain, `docs/DATASETS.md`) — they're mounted at
   `/data` / `L2_MODELS_DIR` by a deployment that has them, not shipped.
3. **`docker-compose.yml` requires real secrets with no defaults** (`JWT_SECRET`, `POSTGRES_PASSWORD`,
   `ADMIN_EMAIL`/`ADMIN_PASSWORD` all use `${VAR:?message}`) — compose refuses to start rather than quietly
   running with a well-known placeholder credential, which is the failure mode that matters for something
   billed as a "one-command demo" that someone might actually leave running.
4. **A `bootstrap` one-shot service provisions the one demo account** via the same `create_user` CLI Phase 9
   already built (`scripts/tasks.py create-user`), chained after `migrate` with
   `depends_on: condition: service_completed_successfully`. No new provisioning code, just reusing what
   exists.
5. **`NEXT_PUBLIC_API_BASE` is a Docker build arg, not a runtime env var**, because Next.js inlines
   `NEXT_PUBLIC_*` values into the client JS bundle at build time — passing it at `docker run` instead would
   silently do nothing.
6. **Verification lives entirely in CI's new `docker` job**, not on the machine that wrote this. It builds
   both images, brings the full stack up with `docker compose up -d --wait` (which polls each service's
   `HEALTHCHECK`), and then does a real smoke test: hits `/api/v1/health` and `/api/v1/channels`, loads
   `/login`, logs in as the bootstrap-provisioned admin account, and calls `/api/v1/auth/me` with the returned
   token. "The containers started" is not the bar; "a real request round-trip works" is.

## Consequences
- The demo, as shipped, cannot run real L2 detection or the live agent without a custom build — stated in
  `docs/LIMITATIONS.md` and `docs/PROGRESS.md`, not just in this ADR.
- `docker compose up --build` has literally never been run by a human on this project, only by CI. If it
  breaks in a way CI's specific smoke test doesn't happen to exercise, nobody would know until someone tries
  it. This is a real gap, named rather than hidden.
- Postgres credentials and the demo admin account are provided by the person running it, via `.env` — there
  is no "it just works with no configuration" path, on purpose (see decision 3).
