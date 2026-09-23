# Development guide

## Commands

Everything goes through `scripts/tasks.py`; the `Makefile` is a thin wrapper (Windows has no `make` by default).

| Task | Command | What it does |
|---|---|---|
| Install | `python scripts/tasks.py setup` | `uv sync --all-groups` (Python 3.12) |
| Lint | `python scripts/tasks.py lint` | `ruff check` + `ruff format --check` |
| Format | `python scripts/tasks.py format` | auto-fix and format |
| Types | `python scripts/tasks.py typecheck` | `mypy` in strict mode |
| Test | `python scripts/tasks.py test` | `pytest` with coverage |
| All | `python scripts/tasks.py check` | what CI runs for Python |
| Data | `python scripts/tasks.py data` | download + checksum real datasets (`DATA_PROFILE=full` adds bearings) |
| Migrate | `python scripts/tasks.py migrate` | Alembic `upgrade head` on `DATABASE_URL` (SQLite by default) |
| Seed | `python scripts/tasks.py seed` | migrate + ingest a replayed mission session |
| Serve | `python scripts/tasks.py serve` | run the API on localhost:8000 (unauthenticated: dev only) |

Targets for training and the demo are added when the code behind them exists.

PostgreSQL/TimescaleDB tests (`-m postgres`) need `POSTGRES_URL` and run in CI only.

## Layout and dependency direction

```
backend/sentinel_core   domain: packets, detectors, attribution      (imports nothing else)
backend/sentinel_agent  AI investigation agent, read-only tools      (imports core only, no DB/API)
backend/sentinel_api    FastAPI app, DB, auth                        (imports core, sim, agent)
simulator/sentinel_sim  replay engine, side channels, scenarios      (imports core)
ml/sentinel_ml          training, evaluation, ONNX export            (imports core, sim)
frontend/               Next.js app (added in the frontend phase)
tests/                  mirrors the packages; fixed seeds everywhere
docs/                   architecture, ADRs, datasets, evaluation, progress
```

## Conventions

- Python 3.12, `mypy --strict`, ruff with security rules enabled.
- Tests use fixed seeds. Slow or networked tests are marked (`slow`, `network`, `postgres`).
- **No fabricated numbers.** Metrics in docs/UI trace to a saved run (seed, dataset, profile, commit).
- Synthetic data is labeled `synthetic` in the DB, API, UI and docs.
- Conventional commits; one logical change per commit.
- Design decisions go in `docs/adr/NNNN-title.md` (short: context, decision, consequences).

## Resuming work

`docs/PROGRESS.md` records the last completed phase and what comes next.
