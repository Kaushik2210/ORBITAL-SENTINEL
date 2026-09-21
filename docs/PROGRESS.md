# Progress log

Newest entry last. Each phase ends with tests green, lint/typecheck clean, a commit, and an entry here.

**Resume pointer:** last completed phase → **Phase 3**. Next → **Phase 4 (database)**.

## Phase roadmap

| # | Phase | Status |
|---|---|---|
| 0 | Bootstrap | done |
| 1 | Research NASA datasets and APIs | done |
| 2 | System architecture and ADRs | done |
| 3 | Data ingestion, packet layer, replay engine | done |
| 4 | Database (Timescale schema, migrations) | next |
| 5 | Detection L1–L5, ML, attribution, scenarios, evaluation | – |
| 6 | Backend APIs (REST, WebSocket, SSE) | – |
| 7 | Mission Control frontend | – |
| 8 | AI investigation agent | – |
| 9 | Platform security | – |
| 10 | Test suites and coverage gates | – |
| 11 | Docker and CI hardening | – |
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
