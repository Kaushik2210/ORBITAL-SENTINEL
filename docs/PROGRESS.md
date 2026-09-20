# Progress log

Newest entry last. Each phase ends with tests green, lint/typecheck clean, a commit, and an entry here.

**Resume pointer:** last completed phase → **Phase 0**. Next → **Phase 1 (dataset research)**.

## Phase roadmap

| # | Phase | Status |
|---|---|---|
| 0 | Bootstrap | done |
| 1 | Research NASA datasets and APIs | next |
| 2 | System architecture and ADRs | – |
| 3 | Data ingestion, packet layer, replay engine | – |
| 4 | Database (Timescale schema, migrations) | – |
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
  2026-09-20. A Hugging Face mirror (`appleparan/telemanom`, BSD-3-Clause) is the planned fallback; it must be
  verified in Phase 1 before use.

## Log

### Phase 0 — Bootstrap
- Nested git repo, MIT license, contributor docs, security policy, issue/PR templates, CODEOWNERS, Dependabot.
- Python tooling: uv, ruff, mypy (strict), pytest + coverage, pre-commit. Cross-platform task runner.
- CI: ruff, mypy, pytest on Ubuntu. Frontend CI job is added with the frontend in Phase 7.
- First domain module: incident taxonomy (five classes + `needs_human`).
- **Next:** Phase 1, download and inspect the real datasets; write `docs/DATASETS.md`.
