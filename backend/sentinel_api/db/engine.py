"""Async engine/session factory; the dialect comes from DATABASE_URL (SQLite or Postgres)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

DEFAULT_URL = "sqlite+aiosqlite:///./data/sentinel.sqlite"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def make_engine(url: str | None = None) -> AsyncEngine:
    url = url or database_url()
    if url.startswith("sqlite"):
        # The default file lives under data/ (gitignored); make sure the folder exists.
        path = url.split(":///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn: object, _rec: object) -> None:
            cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        try:
            yield s
            await s.commit()
        except BaseException:
            await s.rollback()
            raise


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly from the models (tests and quick local runs; prod uses Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
