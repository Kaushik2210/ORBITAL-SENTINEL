"""Small write helpers shared by the seed script, the API and tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_sim.mission import ChannelSpec

from .models import Channel
from .models import Session as SessionRow


async def register_channels(
    factory: async_sessionmaker[AsyncSession], catalog: list[ChannelSpec]
) -> None:
    """Idempotently insert/update the channel catalog."""
    async with factory() as s:
        for c in catalog:
            await s.merge(
                Channel(
                    id=c.id,
                    family=c.family,
                    subsystem_group=c.group,
                    group_is_synthetic_grouping=c.group_is_synthetic_grouping,
                    source=c.source,
                    unit=c.unit,
                    synthetic=c.synthetic,
                )
            )
        await s.commit()


async def create_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    seed: int,
    start: datetime,
    speed: float,
    synthetic: bool,
    scenario_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    sid = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            SessionRow(
                id=sid,
                kind=kind,
                scenario_id=scenario_id,
                seed=seed,
                start=start,
                speed=speed,
                status="created",
                config=config or {},
                synthetic=synthetic,
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
    return sid
