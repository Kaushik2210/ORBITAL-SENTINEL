"""An append-only, hash-chained audit log: each row commits to a SHA-256 of the previous row's
hash plus its own fields, so deleting or editing a past row (short of rewriting everything after
it) breaks ``verify_chain``. This is application-enforced, not DB-enforced (ADR: TimescaleDB/SQLite
here have no portable "append only" constraint) — a DBA with write access could still tamper and
recompute the chain; the guarantee is against casual edits and against a row going missing without
every later hash changing, not against a fully compromised database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import AuditLog

GENESIS = "0" * 64


def _row_hash(
    prev_hash: str, ts: datetime, actor: str, action: str, target: str, detail: dict[str, Any]
) -> str:
    payload = json.dumps(
        {
            "prev": prev_hash,
            "ts": ts.isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLedger:
    """Serializes writes (an ``asyncio.Lock``) so concurrent requests cannot race on ``prev_hash``
    within one process. A multi-worker deployment would need a DB-level serialization point
    instead; see docs/THREAT_MODEL.md.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._lock = asyncio.Lock()

    async def record(
        self, actor: str, action: str, target: str, detail: dict[str, Any] | None = None
    ) -> None:
        detail = detail or {}
        async with self._lock, self._factory() as db:
            last = (
                await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))
            ).scalar_one_or_none()
            prev_hash = last.hash if last else GENESIS
            ts = datetime.now(UTC)
            row_hash = _row_hash(prev_hash, ts, actor, action, target, detail)
            db.add(
                AuditLog(
                    ts=ts,
                    actor=actor,
                    action=action,
                    target=target,
                    detail=detail,
                    prev_hash=prev_hash,
                    hash=row_hash,
                )
            )
            await db.commit()

    async def verify(self) -> tuple[bool, int | None]:
        """Returns ``(True, None)`` if the chain is intact, else ``(False, <first bad row id>)``."""
        async with self._factory() as db:
            rows = (await db.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
        prev = GENESIS
        for row in rows:
            ts = row.ts.replace(tzinfo=UTC) if row.ts.tzinfo is None else row.ts
            expected = _row_hash(prev, ts, row.actor, row.action, row.target, dict(row.detail))
            if row.prev_hash != prev or row.hash != expected:
                return False, row.id
            prev = row.hash
        return True, None
