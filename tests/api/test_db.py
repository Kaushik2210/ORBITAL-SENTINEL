from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sentinel_api.db import models as m
from sentinel_api.db.engine import create_all, make_engine, make_session_factory
from sentinel_api.db.repo import create_session, register_channels
from sentinel_api.db.seed import seed
from sentinel_api.db.sink import DbSink
from sentinel_core.events import AuthEvent, CommandEvent, Event, MalformedFrameEvent
from sentinel_core.ingest import AsyncIngest, Ingestor, RawFrame
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import pcoe
from sentinel_sim.mission import Injector, Mission, MissionConfig
from sentinel_sim.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[2]
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def alembic_cfg() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def test_migration_creates_every_model_table_and_matches_the_models(db_url: str) -> None:
    command.upgrade(alembic_cfg(), "head")
    eng = create_engine(db_url.replace("+aiosqlite", ""))
    assert set(inspect(eng).get_table_names()) - {"alembic_version"} == set(m.Base.metadata.tables)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), m.Base.metadata)
    assert diff == [], f"models and migration disagree: {diff}"


def test_migration_downgrade_removes_everything(db_url: str) -> None:
    command.upgrade(alembic_cfg(), "head")
    command.downgrade(alembic_cfg(), "base")
    eng = create_engine(db_url.replace("+aiosqlite", ""))
    assert set(inspect(eng).get_table_names()) <= {"alembic_version"}


def test_hypertable_tables_have_time_in_their_primary_key() -> None:
    """TimescaleDB requires unique keys to include the partition column."""
    for name in m.HYPERTABLES:
        pk = {c.name for c in m.Base.metadata.tables[name].primary_key.columns}
        assert "ts" in pk, name


def test_tables_that_can_hold_generated_data_have_a_synthetic_flag() -> None:
    names = (
        *("sessions", "channels", "telemetry", "packets", "link_metrics"),
        *("command_log", "auth_events", "incidents", "eval_runs"),
    )
    for name in names:
        assert "synthetic" in m.Base.metadata.tables[name].c, name


def test_telemetry_has_no_ground_truth_column() -> None:
    cols = set(m.Base.metadata.tables["telemetry"].c.keys())
    leaks = {c for c in cols if any(w in c for w in ("inject", "attack", "truth", "label"))}
    assert not leaks


class FloodAndReplay(Injector):
    """Duplicates every frame at step 2 and re-sends step 1's frames at step 3."""

    def __init__(self) -> None:
        self.prev: list[RawFrame] = []

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        out = frames
        if k == 2:
            out = frames * 5
        if k == 3:
            now = k * STEP_SECONDS  # a replayed frame arrives now but claims its old packet time
            out = [*frames, *(replace(f, ts_rx=now) for f in self.prev)]
        if k == 1:
            self.prev = frames
        return out


def make_mission(injectors: tuple[Injector, ...] = ()) -> Mission:
    return Mission(
        MissionConfig(seed=3),
        injectors=injectors,
        battery=pcoe.parametric_battery(),
        wheel=pcoe.parametric_wheel(),
    )


async def ingest_into_db(
    mission: Mission, steps: int, extra: list[Event] | None = None
) -> tuple[AsyncEngine, Factory, str]:
    engine = make_engine()
    await create_all(engine)
    factory = make_session_factory(engine)
    await register_channels(factory, mission.catalog())
    sid = await create_session(
        factory, kind="scenario", seed=3, start=mission.config.start, speed=0.0, synthetic=True
    )
    sink = DbSink(factory, sid, mission.config.start, batch_size=50)
    ing = AsyncIngest(
        Ingestor(mission.apids, mission.config.frame_key, mission.config.start), [sink]
    )
    await ing.run(ReplayEngine(mission, speed=0.0, max_steps=steps).stream())
    for e in extra or []:
        await sink(e)
    await sink.flush()
    return engine, factory, sid


async def count(factory: Factory, model: Any, *where: Any) -> int:
    async with factory() as s:
        q = select(func.count()).select_from(model)
        for w in where:
            q = q.where(w)
        return (await s.execute(q)).scalar_one()


def test_duplicated_and_replayed_frames_are_all_stored_without_key_collisions(
    db_url: str,
) -> None:
    async def go() -> tuple[int, int, int]:
        engine, factory, _ = await ingest_into_db(make_mission((FloodAndReplay(),)), 6)
        out = (
            await count(factory, m.PacketRow),
            await count(factory, m.Telemetry),
            await count(factory, m.PacketRow, m.PacketRow.ts_pkt < m.PacketRow.ts),
        )
        await engine.dispose()
        return out

    packets, telemetry, stale = asyncio.run(go())
    # 2 APIDs * 6 steps, +8 duplicates at step 2 (4 extra copies * 2 APIDs), +2 replayed at step 3
    assert packets == 12 + 8 + 2
    assert telemetry == packets * 7  # 7 channels per APID
    assert stale == 2  # replayed frames claim an older packet time than their receive time


def test_attacker_controlled_strings_are_length_bounded(db_url: str) -> None:
    extra: list[Event] = [
        CommandEvent(1.0, 1, "X" * 5000, "Y" * 5000, False, False),
        AuthEvent(1.0, "Z" * 5000, False),
        MalformedFrameEvent(1.0, 3, "W" * 5000),
    ]

    async def go() -> tuple[int, int, int]:
        engine, factory, _ = await ingest_into_db(make_mission(), 1, extra=extra)
        async with factory() as s:
            cmd = (
                (await s.execute(select(m.CommandLog).where(m.CommandLog.opcode == 1)))
                .scalars()
                .one()
            )
            auth = (
                (await s.execute(select(m.AuthEventRow).where(m.AuthEventRow.success.is_(False))))
                .scalars()
                .all()
            )
            bad = (await s.execute(select(m.MalformedFrame))).scalars().one()
        await engine.dispose()
        return len(cmd.opcode_name), max(len(a.source) for a in auth), len(bad.reason)

    assert asyncio.run(go()) == (64, 128, 200)


def test_foreign_keys_are_enforced_on_sqlite(db_url: str) -> None:
    async def go() -> None:
        engine = make_engine()
        await create_all(engine)
        factory = make_session_factory(engine)
        now = datetime.now(UTC)
        async with factory() as s:
            s.add(
                m.Telemetry(
                    session_id="nope",
                    ts=now,
                    n=1,
                    channel_id="x",
                    ts_pkt=now,
                    value=0.0,
                    synthetic=True,
                )
            )
            with pytest.raises(IntegrityError):
                await s.commit()
        await engine.dispose()

    asyncio.run(go())


def test_register_channels_is_idempotent_and_flags_synthetic(db_url: str) -> None:
    async def go() -> list[m.Channel]:
        engine = make_engine()
        await create_all(engine)
        factory = make_session_factory(engine)
        cat = make_mission().catalog()
        await register_channels(factory, cat)
        await register_channels(factory, [replace(cat[0], unit="V2")])  # update in place
        async with factory() as s:
            rows = list((await s.execute(select(m.Channel))).scalars().all())
        await engine.dispose()
        return rows

    rows = asyncio.run(go())
    assert len(rows) == 14
    assert next(r for r in rows if r.id == "batt_v_a").unit == "V2"
    assert all(r.synthetic for r in rows)


def test_seed_end_to_end_counts_are_consistent(db_url: str) -> None:
    command.upgrade(alembic_cfg(), "head")
    out = asyncio.run(seed(steps=20, smap=(), seed_value=1, data_root=Path("nonexistent")))
    assert out["ticks"] == 20
    assert out["rows"] == out["events"]  # every event became exactly one row
