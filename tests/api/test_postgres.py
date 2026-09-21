"""PostgreSQL + TimescaleDB checks. Run only in CI (or locally with POSTGRES_URL set).

Not verified on the development machine (no Docker/Postgres): the CI `postgres` job is the source of
truth for the hypertable, compression and retention behavior described in ADR 0007.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from sentinel_api.db.models import HYPERTABLES

POSTGRES_URL = os.environ.get("POSTGRES_URL")  # e.g. postgresql+asyncpg://u:p@localhost:5432/db
ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(POSTGRES_URL is None, reason="POSTGRES_URL not set"),
]


@pytest.fixture
def migrated(monkeypatch: pytest.MonkeyPatch) -> str:
    assert POSTGRES_URL is not None
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return POSTGRES_URL


def sync_engine(url: str) -> Engine:
    # asyncpg is async-only; a synchronous inspection engine needs a sync driver in CI.
    return create_engine(url.replace("+asyncpg", "+psycopg"))


def test_time_series_tables_are_hypertables(migrated: str) -> None:
    with sync_engine(migrated).connect() as conn:
        rows = conn.execute(text("SELECT hypertable_name FROM timescaledb_information.hypertables"))
        found = {r[0] for r in rows}
    assert set(HYPERTABLES) <= found


def test_compression_and_retention_policies_exist(migrated: str) -> None:
    with sync_engine(migrated).connect() as conn:
        rows = conn.execute(
            text(
                "SELECT hypertable_name, proc_name FROM timescaledb_information.jobs "
                "WHERE proc_name IN ('policy_compression', 'policy_retention')"
            )
        ).all()
    by_table: dict[str, set[str]] = {}
    for name, proc in rows:
        by_table.setdefault(name, set()).add(proc)
    for table in HYPERTABLES:
        assert by_table.get(table) == {"policy_compression", "policy_retention"}, table
