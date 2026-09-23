# Progress log

Newest entry last. Each phase ends with tests green, lint/typecheck clean, a commit, and an entry here.

**Resume pointer:** last completed phase → **Phase 11 (Docker/CI hardening)**. Next → **Phase 12 (documentation)**.

## Phase roadmap

| # | Phase | Status |
|---|---|---|
| 0 | Bootstrap | done |
| 1 | Research NASA datasets and APIs | done |
| 2 | System architecture and ADRs | done |
| 3 | Data ingestion, packet layer, replay engine | done |
| 4 | Database (Timescale schema, migrations) | done |
| 5 | Detection L1–L5, ML, attribution, scenarios, evaluation | done |
| 6 | Backend APIs (REST, WebSocket, SSE) | done (JWT auth added in Phase 9) |
| 7 | Mission Control frontend | done (scope cut: no 3D globe/animation library — see `frontend/README.md`) |
| 8 | AI investigation agent | done (offline fallback tested end-to-end; live LLM path tested against a stub client only) |
| 9 | Platform security | done (JWT + roles, rate limiting, hash-chained audit log, security headers, CI scanners advisory-only) |
| 10 | Test suites and coverage gates | done (Vitest units, Playwright e2e against a real API, backend coverage floor enforced at 75%) |
| 11 | Docker and CI hardening | done (`docker compose up --build`; verified in CI only, no Docker on the dev machine) |
| 12 | Documentation | – |

## Environment notes (measured on the dev machine)

- Windows 11, 16 logical cores, ~15.6 GB RAM, no NVIDIA GPU: training is CPU-only.
- Python 3.12 via `uv`; Node 22 / npm 11. `make` and Docker are **not installed** here, so Docker/Timescale
  artifacts are validated by CI only, and local runs use SQLite.
- The original Telemanom S3 archive (`s3-us-west-2.amazonaws.com/telemanom/data.zip`) returned HTTP 403 on
  2026-09-20. The pinned Hugging Face mirror (`appleparan/telemanom`) is now the working source (verified).

## Log

### Phase 0 — Bootstrap
- Nested git repo, MIT license, contributor docs, security policy, issue/PR templates, CODEOWNERS, Dependabot.
- Python tooling: uv, ruff, mypy (strict), pytest + coverage, pre-commit. Cross-platform task runner.
- CI: ruff, mypy, pytest on Ubuntu. Frontend CI job is added with the frontend in Phase 7.
- First domain module: incident taxonomy (five classes + `needs_human`).
- **Next:** Phase 1, download and inspect the real datasets; write `docs/DATASETS.md`.

### Phase 1 — Dataset research
- Built `sentinel_sim.datasets` (streamed, atomic, SHA-256 manifest, ordered source chain, `lite`/`full`
  profiles) and `python scripts/tasks.py data`. 20 unit tests use a mocked HTTP transport.
- Downloaded and inspected all real datasets (SMAP/MSL 173 MB, battery 210 MB, bearings 1.08 GB); probed DONKI.
  Reports are in `docs/data/`; the analysis is in `docs/DATASETS.md`.
- **Findings that change the plan:**
  - Original SMAP/MSL S3 archive is 403; using the pinned HF mirror. Primary-archive path is untested against the real archive.
  - Command columns are binary **multi-hot**, not one-hot. `T-10` is unlabeled, `P-2` has two label rows, 16 channels have
    a constant training signal, and 43/82 channels' test values leave the training range: detectors must handle all of it.
  - Most PCoE battery cells are not clean aging curves; use B0005/B0006/B0007/B0018.
  - IMS bearings are a 7z of RARs; RMS alone does not separate the failing bearing cleanly.
  - `DEMO_KEY` DONKI limit is 10 requests (measured), so the client must cache hard and prefetch few wide windows.
  - PCoE has no formal license: derived data is not committed; the synthetic fallback matters.
- **Not done yet (Phase 3):** the synthetic-telemetry fallback generator, the bearing/battery loaders, the DONKI client
  and its prefetch. About 6 of the 10 `DEMO_KEY` requests were used during inspection.
- **Next:** Phase 2, `docs/ARCHITECTURE.md` (Mermaid, DB schema, API contract, detector interfaces, ADRs).

### Phase 2 — Architecture
- `docs/ARCHITECTURE.md`: context and package diagrams (Mermaid), packet layout, detector interface, layer design,
  attribution and confusable-pair table, scenario YAML schema, DB schema and ER diagram, API contract, agent,
  frontend and a verified-where matrix. ADRs 0004-0009.
- **Design decision driven by Phase 1:** two telemetry families (ADR 0004). SMAP/MSL is real but anonymized (L1/L2 only);
  a synthetic EPS/wheel bus driven by real PCoE trajectories carries redundancy and physics (L3) and all scenario classes.
- **Next:** Phase 3. Order: packet codec + tests, side-channel generators, synthetic bus, replay engine,
  DONKI client, loaders + fallback generator, async ingestion.

### Phase 3 — Ingestion, packet layer, replay engine
- `sentinel_core`: `packets` (CCSDS-like codec, HMAC tag, 14-bit wrap arithmetic), `events`, `timebase`
  (60 s/step is a *simulation convention*), `ingest` (forgiving decoder + bounded-queue async fan-out that
  surfaces producer/sink failures).
- `sentinel_sim`: `smap_msl` and `pcoe` loaders (real data with labeled `synthetic-parametric` fallbacks),
  `donki` (permanent cache, 30-day windows, typed 429), `bus` (EPS + wheels driven by real PCoE/IMS trajectories,
  redundant sensors, exact power balance), `sidechannels` (commands/auth/link, synthetic), `mission` (packetizer
  with three injection hooks: sensor / link / ground), `replay` (play/pause/speed/seek).
- Measured while building: real B0005 resistance grows x1.22 (not the x1.6 first assumed; fallback corrected);
  the IMS loader reproduces the Phase 1 RMS ratio 2.80 from the real archive; a corrupted timestamp field makes a
  frame *malformed* while a payload flip makes it *unauthenticated* (both are detectable, differently).
- **Fallback behavior:** if SMAP/MSL is missing the mission runs bus-only (all synthetic, labeled); if PCoE/IMS is
  missing the bus uses `synthetic-parametric` trajectories. No separate SMAP-like generator is built: it would add
  synthetic data that could be confused with the real set.
- **Not done:** DONKI prefetch is deferred to Phase 5 (needs ~2 requests for the May 2024 SEU scenario; the DEMO_KEY
  budget was mostly spent in Phase 1). DB writer sink comes in Phase 4.
- **Next:** Phase 4: SQLAlchemy 2 models, Alembic migrations (Timescale-guarded), seed/backfill, DB sink for `AsyncIngest`.

### Phase 4 — Database
- `sentinel_api.db`: dialect-neutral SQLAlchemy 2 models (17 tables), async engine (SQLite pragmas: FKs on, WAL),
  batching `DbSink` for `AsyncIngest`, repo helpers, and a seed CLI that ingests a replayed mission.
- Alembic (async env, URL from `DATABASE_URL`): initial migration autogenerated, plus a PostgreSQL-only, extension-guarded
  section creating hypertables (1-day chunks), compression (7 days) and retention (30 days).
- Design fix found by tests: telemetry is keyed by *receive* time + arrival index (packet-claimed time is a separate
  column) so replayed/flooded frames never collide. Scenario injectors that replay frames must stamp the current
  receive time (`replace(frame, ts_rx=now)`) while keeping the old packet time.
- Verified locally (SQLite): migration up/down, models == migration (`compare_metadata`), FK enforcement, replay/flood
  storage, attacker-string truncation. Seed of 300 steps: 4,800 telemetry rows (16 ch x 300), 1,200 packets.
- **Not verified locally:** Timescale hypertables/policies (`tests/api/test_postgres.py`, CI `postgres` job only).
- **Next:** Phase 5. Order: detector interface + L4 protocol (cheapest, most deterministic), L1, L3, L5, scenario engine +
  YAML scenarios, then feature builder + attribution, then L2 forecaster (PyTorch->ONNX), then evaluation runs.

### Phase 5 (in progress) — detection engine
- Done: detector framework (`sentinel_core.detection`: `Detector` with calibrate/learn/freeze, `DetectorOutput`+`Evidence`,
  `DetectionEngine` with flush and stage-2 observers, `IncidentBuilder`); **L4** protocol/security (sequence with wrap,
  timestamp freshness, auth tag/malformed, command policy, auth anomaly, link shift, rate anomaly); **L1** statistical
  (range, residual z, level shift, detrended CUSUM, rate-of-change, flatline, variance, dropout); **L3** redundant
  sensors with shape/side evidence, and power balance. 84 core tests.
- Findings while building (all fixed, all in tests): CUSUM on non-stationary voltage produced 871 false alarms per
  3,000 steps -> detrended CUSUM (ADR 0010); live detrending reference started at one noisy sample -> start-up
  false alarm; mission start-up transient -> `inhibit_s`; CUSUM threshold too tight -> h >= 10 sigma.
- Not done yet: L5 environmental, scenario engine (+ SPARTA/ATT&CK tags, which must be verified from the source, not
  recalled), feature builder + attribution, L2 forecaster (PyTorch -> ONNX) + Isolation Forest, evaluation runs
  and `docs/EVALUATION.md`.
- Scenario engine done: 33 YAML scenarios (`data/scenarios/`, generated by `scripts/gen_scenarios.py`) covering all 7 attack
  families, sensor stuck/noise/dropout/drift, battery degradation and bearing wear (real PCoE/IMS trajectories), SEU aligned
  with a real DONKI SEP event, 3 nominal runs, and 18 `hard` confusable scenarios. Schema validation is strict (unknown
  effects, missing params, windows, keyed attacks, unverified tags all rejected). SPARTA/ATT&CK ids were **verified against the
  live sites** (SPARTA v4.0.1, MITRE ATT&CK) on 2026-09-21; SPARTA has no DoS/brute-force technique, so those use ATT&CK only.
- L5 done: weather overlap (real DONKI: 3 requests cached FLR/SEP/GST for 2024-05-01..30) and simultaneous-upset. A 58.7 h
  quiet window (24 May 20:25 -> 27 May 07:08 UTC, no M/X flare, SEP or storm) is the default session start.
- Pipeline + parallel suite runner (`sentinel_ml.pipeline/batch`): whole 33-scenario suite runs in ~35 s on 8 workers.
- Bugs found by running the suite (fixed, tested): flood duplicates tripped L1 flatline (36,400 fires) -> one sample per
  channel per step; exponent bit-flips saturated CUSUM -> winsorised inputs; DONKI cache did not serve sub-ranges of a cached
  span -> fixed; my off-window login landed inside a pass; the "hide the wear" spoof did not hide anything.
- Not done yet: feature builder + attribution model (ADR 0006), L2 forecaster/Isolation Forest, evaluation runs and
  `docs/EVALUATION.md` (all numbers must come from those runs).
- Attribution done (ADR 0006): 32 evidence features (`sentinel_core.attribution.features`), exact-contribution logistic
  regression with temperature scaling and validation-tuned abstention (`model.py`, pure NumPy at inference), variants
  (`instantiate`), dataset builder, training, and a held-out evaluation. 462 runs -> 646 incident windows. The test split
  (variants 10-13) was scored once after iterating on validation only. Results are in `docs/EVALUATION.md`
  (auto-rendered from `docs/data/evaluation_v1.json`); the trained model is `models/attribution-v1.json`.
- Headline (see the report for CIs and caveats): forced-choice 86.4%; 95.1% on answered windows at 61.8% coverage; hand
  rules 51.8%. Hard pairs: replay vs stuck, jamming vs dropout and SEU vs injected glitches are separated; the slow
  single-sensor drift family (spoof / drift / low-and-slow / ramp) is never separated and always ends `needs_human`.
  Leave-one-scenario-out shows the model does not generalize to unseen failure modes.
- **Not done in Phase 5:** L2 (LSTM forecaster -> ONNX, Isolation Forest) and SMAP/MSL event-level evaluation of any
  detector on the real anomalies. Without them the platform makes no claim about real operational anomalies yet.
- CI now installs the `ml` extra (scikit-learn).
- Real-data evaluation added (`sentinel_ml.smap_eval`, ~10 s): L1 alone on the 81 labeled SMAP/MSL channels, event level:
  F1 0.64 overall but only **0.55 on the 65 channels with a varying training signal** (the 16 constant-training channels
  score 1.00 trivially). Rendered into `docs/EVALUATION.md`; raw numbers in `docs/data/smap_msl_l1_v1.json`.

### L2 forecaster and the API (this session)
- **L2:** `sentinel_ml.forecaster` (2x80 LSTM per channel, inputs `[x_t, c_(t+1)]` with multi-hot commands, ONNX export with
  a dynamic batch axis, verified against PyTorch at batch 1 and many), `sentinel_core.detection.dynamic_threshold`
  (Hundman et al. dynamic thresholding + pruning, pure NumPy), and the streaming `sentinel_core.detection.l2.ForecasterDetector`
  (ONNX Runtime, imported lazily; reproduces the batch errors exactly). Trained for all 81 labeled channels
  (15-epoch cap vs the paper's 35, ~12 min on 6 workers); ONNX files are under `ml/runs/l2-v1/onnx` (gitignored).
- **Real-data result (rendered in `docs/EVALUATION.md`):** on the 65 varying-signal channels L2 alone F1 0.40 < L1 0.55; Isolation
  Forest baseline 0.12; L1+L2 0.57 (within noise). Not a clear win; a fully trained forecaster is untested.
- **Layering fix:** `pipeline.py` moved to `sentinel_sim` and the L2 detector to `sentinel_core` so the API never imports
  `sentinel_ml` (ADR 0002 holds).
- **API** (`docs/API.md`): sessions (scenario and real SMAP/MSL replay), incidents with exact-contribution evidence, telemetry,
  WebSocket + SSE, ground truth withheld until the run finishes, evaluation and dataset endpoints. 26 API tests, including a
  real-data replay through L1 + ONNX L2 that finds an incident overlapping a labeled real anomaly. Found and fixed: a slotted
  dataclass serialization bug that failed whole runs.
- **Deliberately not applied to real channels:** attribution. Replay incidents are `needs_human` with a note.

### AI investigation agent (this session)
- New package `backend/sentinel_agent`, importing only `sentinel_core` (never the API or a database — ADR 0002
  extended: api -> agent, agent has no other dependency). `case.py` defines a frozen `Case` data bundle;
  `tools.py` has eight pure, read-only functions over it (`get_incident_summary`, `get_evidence`,
  `get_detector_outputs`, `get_telemetry_window`, `get_channel_baseline`, `get_related_incidents`,
  `get_space_weather_context`, `get_packet_integrity`) plus their Anthropic tool-use JSON schemas.
- `report.py`: a Pydantic `Report` schema (verdict/confidence/summary/evidence/action/caveats) that is the
  *only* way an investigation can end — `submit_report`'s arguments are validated against it; free text alone
  never ends the loop (ADR 0008).
- `offline.py`: deterministic template report from the same evidence, no LLM. This is the default (no
  `ANTHROPIC_API_KEY`) and the only path tested end-to-end.
- `client.py`: the tool-use loop (`claude-sonnet-5` default, 6-turn cap), with the offline report as the
  fallback at every failure point (no key, SDK missing, network error, model never calling `submit_report`,
  invalid final arguments). Added `anthropic` as an optional `agent` extra so the default install stays light.
- `backend/sentinel_api/investigate.py` assembles a `Case` from the DB (incident, evidence, detector outputs,
  telemetry ± 30 min margin, session baseline, related incidents, cached DONKI overlap, packet/auth integrity
  counts) and renders the report to Markdown. New endpoints `POST /incidents/{id}/investigate` and
  `GET /incidents/{id}/report`, persisted to the already-designed `Report`/`InvestigationEvent` tables.
- 21 agent unit tests (offline determinism, tool whitelist, Pydantic validation, a stub-client tool-use loop
  covering an unknown-tool call, an invalid `submit_report`, and text-only output — all fall back safely
  instead of being trusted) plus 3 new API tests. Not built: PDF export, a streaming SSE trace (the full trace
  is returned once the investigation finishes), and any live-key verification.

### Platform security (this session)
- **Auth:** `backend/sentinel_api/security/`: `passwords.py` (PBKDF2-HMAC-SHA256, 600k iterations, random salt,
  stdlib only), `tokens.py` (HS256 JWTs via `pyjwt`, three roles `viewer < analyst < admin`), `deps.py`
  (`require_role(...)` FastAPI dependency; `PUBLIC_DEMO_MODE` only ever relaxes an anonymous `GET` at `viewer`
  level — every mutating or admin route always needs a real token). `POST /auth/login`, `GET /auth/me`; no
  self-registration endpoint — `scripts/tasks.py create-user` provisions accounts directly in the DB.
- **Audit log:** `audit.py`'s `AuditLedger` — each row's hash commits to the previous row's hash plus its own
  fields (login attempts, session create/control, investigate calls are recorded); `GET /audit` and
  `GET /audit/verify` (admin only). Application-enforced, not DB-enforced — documented honestly in
  `docs/THREAT_MODEL.md`.
- **Rate limiting:** `ratelimit.py`, a per-client-IP in-memory sliding window (`RATE_LIMIT_PER_MINUTE`); explicitly
  documented as not correct across multiple worker processes.
- **Headers:** `headers.py` adds CSP (exempting `/docs`/`/redoc`), `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy` to every response.
- **`docs/THREAT_MODEL.md`**: a full STRIDE table plus a dedicated section on the agent's prompt-injection surface.
- **CI:** a new `security` job runs `bandit` and `pip-audit` (`continue-on-error: true` — advisory, not a merge
  gate yet).
- 19 new tests in `tests/api/test_security.py` (password hashing, token round-trip/tampering, login, `/me`,
  demo-mode read bypass vs. always-authenticated writes, per-role enforcement, audit-chain tamper detection,
  security headers, rate-limit 429). All existing API tests updated to bootstrap and use an admin token.

### Mission Control frontend (this session)
- `frontend/`: Next.js 16 (App Router, Turbopack), TypeScript strict, Tailwind v4, `zustand` for the auth
  store, `uplot` for streaming telemetry. No code generation — `lib/types.ts` mirrors
  `backend/sentinel_api/schemas.py` by hand and `lib/api.ts` is a small typed fetch wrapper.
- Pages: `/` (readiness + recent incidents), `/login`, `/scenarios` (library + launch, gated to `analyst`),
  `/sessions/[id]` (live WebSocket telemetry chart, incidents-as-raised, a ground-truth reveal button once
  finished), `/incidents` (filterable list), `/incidents/[id]` (posterior, signed evidence, detector outputs,
  and the AI agent's `investigate` button with its offline/live badge and tool-use trace).
- Verified against the real running API end-to-end in the browser, not just built: signed in, launched the
  `auth_bruteforce` scenario, watched telemetry stream and an incident get raised live, opened it, ran the
  agent (offline fallback, no key configured), and confirmed demo-mode read access and the analyst gate on
  launching a scenario both behave as designed.
- Cut from the original brief's scope to ship something real rather than a shell: no `react-three-fiber`
  globe, no Framer Motion, no scored "Attack or Accident?" challenge (the ground-truth reveal button is the
  honest, unscored version). All stated in `frontend/README.md`.
- CI gets a `frontend` job (`eslint`, `vitest`, `next build` — `next build` type-checks itself, so a
  standalone `tsc --noEmit` step was tried and dropped: it fails before Next.js has generated its own route
  types).

### Test suites and coverage gates (this session)
- **Frontend units** (`vitest` + Testing Library, jsdom): `lib/auth-store.ts` (`hasRole` role ordering),
  `lib/api.ts` (success/error/query/bearer-token behavior of the fetch wrapper, mocked `fetch`),
  `components/badges.tsx` and `components/posterior-bars.tsx` (label text, sort order, percentages).
- **End-to-end** (`frontend/e2e/`, Playwright): `global-setup.ts` runs a real, isolated backend for the suite
  — a fresh temp SQLite DB, migrated, one seeded admin user, `RATE_LIMIT_PER_MINUTE=0` and no Anthropic key —
  on port 8123; Playwright's `webServer` starts the Next.js dev server on port 3100 against it.
  `mission-control.spec.ts` signs in for real, launches the `auth_bruteforce` scenario, waits for it to
  actually complete, opens the incident it actually raised, runs the (offline-fallback) agent against it, and
  reveals ground truth — plus a second spec confirming the signed-out demo-mode read/write boundary. Both
  processes are torn down afterward (`global-teardown.ts`). Found and fixed two real bugs along the way: a
  dev-mode cross-origin guard blocking the frontend when bound to `127.0.0.1` (`allowedDevOrigins` in
  `next.config.ts`), and the rate limiter tripping on the API test suite's own fast polling loops (test
  fixtures now set `rate_limit_per_minute=0`).
- **Coverage gate:** `fail_under = 75` in `pyproject.toml` (measured ~82% on 2026-09-23), enforced directly by
  `pytest --cov` — previously `0`, i.e. unenforced.
- CI gets a new `e2e` job (installs Playwright + Chromium, runs the suite against a real backend it starts)
  and the `frontend` job now runs `npm run test:coverage`.

### Docker and CI hardening (this session)
- **`backend/Dockerfile`:** multi-stage (`uv sync --frozen --no-dev` builder → slim Python 3.12 runtime),
  non-root user, `HEALTHCHECK`. Deliberately does **not** install the `ml`/`agent` extras (no torch,
  onnxruntime or anthropic) — the API degrades gracefully without them (no L2 models mounted, offline agent
  fallback), keeping the image lean; a deployment that wants the live paths adds those extras in its own build
  (documented in the Dockerfile itself). Build context is the repo root (uv workspace spans
  `backend`/`simulator`/`ml`), with a root `.dockerignore`.
- **`frontend/Dockerfile`:** multi-stage using Next.js `output: "standalone"`, non-root, `HEALTHCHECK`.
  `NEXT_PUBLIC_API_BASE` is a build arg (it's inlined into the JS bundle at build time, so it has to be known
  before `docker build`, not just at `docker run`).
- **`docker-compose.yml`:** `db` (TimescaleDB) → `migrate` (alembic, one-shot) → `bootstrap` (provisions one
  admin account via `create_user`, one-shot) → `api` → `web`, wired with `depends_on: condition:
  service_healthy` / `service_completed_successfully`. Required secrets (`JWT_SECRET`, `POSTGRES_PASSWORD`,
  `ADMIN_EMAIL`/`ADMIN_PASSWORD`) have no baked-in defaults — compose refuses to start without a filled-in
  `.env` (`.env.example` documents each one).
- **`scripts/tasks.py demo`** / **`make demo`** run `docker compose up --build`.
- **Verification:** a new CI `docker` job (the only place any of this is actually run — the dev machine has no
  Docker) builds both images, brings the whole stack up with `docker compose up -d --wait`, then does a real
  smoke test: health endpoints, a real login against the bootstrap-provisioned admin account, and a real
  `/auth/me` call with the returned token — not just "did it start."

## Resume here (next session)

1. **Phase 12 docs:** `DETECTION.md` (per-detector writeup), a demo script, README screenshots/GIF.
2. Security debts (all in `docs/THREAT_MODEL.md`): no MFA or token revocation list; rate limiter and audit log
   are single-process/application-enforced only; `bandit`/`pip-audit` are advisory, not blocking.
3. Other debts: the L2 models are not committed (train with `python -m sentinel_ml.l2_eval`) and are not baked
   into the Docker image either — the containerized API runs L1/L3-L5 + attribution only unless you mount
   them; DONKI cache is local-only (scenarios fall back to `weather_context = unavailable` without it); the
   agent's live path has no key-based verification, in Docker or otherwise; no frontend error boundary polish
   beyond basic try/catch; the e2e suite covers one path, not every page/role combination; frontend coverage
   has no enforced floor yet; `docker compose up` has never been run outside CI.
