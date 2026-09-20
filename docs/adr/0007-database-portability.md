# 7. Database portability: SQLite locally, PostgreSQL + TimescaleDB in compose and CI

Date: 2026-09-20 · Status: accepted

## Context
The brief specifies PostgreSQL + TimescaleDB. The dev machine has neither Postgres nor Docker.

## Decision
SQLAlchemy 2 models stay dialect-neutral. Alembic migrations create hypertables, compression and retention
policies only when `dialect == postgresql` and the extension is available. Local dev and the default test
run use SQLite; a CI job runs the suite against a TimescaleDB service (`@pytest.mark.postgres`).

## Consequences
- Timescale-specific behavior is verified only in CI; docs say so.
- Avoid dialect-specific SQL in application code; JSON columns use SQLAlchemy's generic `JSON`.
- The audit log's append-only guarantee is DB-enforced on Postgres (revoked `UPDATE/DELETE` plus trigger) and
  application-enforced plus hash-chained on SQLite; a test covers the hash-chain verification.
