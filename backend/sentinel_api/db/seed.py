"""Seed a local database: migrate, register channels, and ingest a replayed mission session.

Usage: ``python -m sentinel_api.db.seed --steps 600 --smap P-1,M-1``. Real SMAP/MSL channels are
optional; with none, the session is entirely synthetic (and is flagged so).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from sentinel_core.ingest import AsyncIngest, Ingestor
from sentinel_sim.mission import Mission, MissionConfig, frame_key_from_env
from sentinel_sim.replay import ReplayEngine

from .engine import make_engine, make_session_factory
from .repo import create_session, register_channels
from .sink import DbSink


def migrate() -> None:
    """Apply Alembic migrations (synchronous entry point: env.py runs its own event loop)."""
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


async def seed(
    steps: int, smap: tuple[str, ...], seed_value: int, data_root: Path
) -> dict[str, int]:
    engine = make_engine()
    factory = make_session_factory(engine)
    mission = Mission(
        MissionConfig(
            seed=seed_value, smap_channels=smap, data_root=data_root, frame_key=frame_key_from_env()
        )
    )
    await register_channels(factory, mission.catalog())
    session_id = await create_session(
        factory,
        kind="replay",
        seed=seed_value,
        start=mission.config.start,
        speed=0.0,
        synthetic=True,  # the physics bus is present, so the session mixes real and synthetic
        config={"smap_channels": list(smap), "steps": steps},
    )
    sink = DbSink(factory, session_id, mission.config.start)
    ingest = AsyncIngest(
        Ingestor(mission.apids, mission.config.frame_key, mission.config.start), [sink]
    )
    stats = await ingest.run(ReplayEngine(mission, speed=0.0, max_steps=steps).stream())
    await sink.flush()
    await engine.dispose()
    return {"ticks": stats.ticks, "events": stats.events, "rows": sink.rows_written}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--smap", default="", help="comma-separated SMAP/MSL channel ids, e.g. P-1,M-1")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--data-root", type=Path, default=Path("data/raw"))
    args = ap.parse_args()
    migrate()
    channels = tuple(c for c in args.smap.split(",") if c)
    print(asyncio.run(seed(args.steps, channels, args.seed, args.data_root)))


if __name__ == "__main__":
    main()
