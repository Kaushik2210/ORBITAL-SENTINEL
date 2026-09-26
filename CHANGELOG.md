# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Project bootstrap: monorepo layout, tooling (ruff, mypy strict, pytest, pre-commit), CI, contributor docs.
- Dataset acquisition (`make data`): checksummed manifest, source fallback chain, `lite`/`full` profiles.
- `docs/DATASETS.md` with measured shapes, counts, licensing and surprises for SMAP/MSL, PCoE battery/bearings and DONKI.
- CCSDS-like packet codec with HMAC tag, typed events, async ingestion, mission replay engine with injection hooks.
- Synthetic physics bus (EPS + reaction wheels, redundant sensors) driven by real PCoE/IMS trajectories.
- DONKI client with permanent cache; loaders for SMAP/MSL and PCoE with labeled fallbacks.
- L2 detectors: per-channel LSTM forecaster with ONNX Runtime serving and dynamic thresholding; Isolation Forest baseline.
- FastAPI service: scenario and real-data replay sessions, incidents with exact evidence, WebSocket and SSE streams.
- AI investigation agent (`sentinel_agent`): a Claude tool-use loop over eight read-only evidence tools, a
  Pydantic-validated final report, and a deterministic offline fallback; `/incidents/{id}/investigate` and
  `/report` endpoints.
- Platform security: JWT auth with three roles, PBKDF2 password hashing, a per-IP rate limiter, security
  response headers, and a hash-chained audit log with tamper detection (`GET /audit/verify`). See
  `docs/THREAT_MODEL.md`.
- Incident taxonomy (`sentinel_core.taxonomy`): five classes plus an explicit `needs_human` verdict.
- Mission Control frontend (`frontend/`, Next.js + TypeScript strict + Tailwind): live scenario launcher,
  streaming telemetry (uPlot over the session WebSocket), incident/evidence browser, and the AI investigation
  agent's report with its offline-vs-live badge and tool-use trace. Verified end-to-end against a running API.
- Test suites: Vitest units for the frontend, a Playwright e2e suite that runs a real isolated backend and
  drives the real browser through sign-in → launch a scenario → an incident → an agent report, and an
  enforced backend coverage floor (`fail_under = 75`, measured ~82%).
- Docker: multi-stage, non-root Dockerfiles for the API and frontend, and `docker-compose.yml` bringing up
  TimescaleDB, migrations, an admin account, the API and the frontend with one command
  (`docker compose up --build` / `python scripts/tasks.py demo`). Verified by a new CI `docker` job (build +
  compose up + a real login smoke test) — never run on the development machine, which has no Docker.
- `docs/DETECTION.md` (the as-built detector reference) and `docs/DEMO.md` (a real walkthrough script); real
  README screenshots of the running platform, captured against the actual dev servers.
