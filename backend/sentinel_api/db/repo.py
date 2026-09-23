"""Small write helpers shared by the seed script, the API and tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_sim.mission import ChannelSpec

from .models import Channel, User
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


async def get_user_by_email(factory: async_sessionmaker[AsyncSession], email: str) -> User | None:
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def upsert_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    role: str,
    password_hash: str,
) -> str:
    """Create the user if new, otherwise update their role and password. Returns the user id."""
    async with factory() as s:
        existing = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing is not None:
            existing.role = role
            existing.password_hash = password_hash
            await s.commit()
            return existing.id
        uid = str(uuid.uuid4())
        s.add(
            User(
                id=uid,
                email=email,
                role=role,
                password_hash=password_hash,
                created_at=datetime.now(UTC),
            )
        )
        await s.commit()
        return uid
