"""Batching database sink for :class:`~sentinel_core.ingest.AsyncIngest`.

Stores what was *received* (including replayed, duplicated and unauthenticated frames), keyed by
receive time plus a per-session arrival index so repeats never collide. Rows are buffered and
written in bulk to keep ingestion fast on both SQLite and PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_core.events import (
    AuthEvent,
    CommandEvent,
    Event,
    LinkEvent,
    MalformedFrameEvent,
    PacketEvent,
    TelemetryEvent,
)

from .models import (
    AuthEventRow,
    CommandLog,
    LinkMetric,
    MalformedFrame,
    PacketRow,
    Telemetry,
)


class DbSink:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        session_id: str,
        start: datetime,
        batch_size: int = 2000,
    ) -> None:
        self.factory = factory
        self.session_id = session_id
        self.start = start
        self.batch_size = batch_size
        self._n = 0
        self._buf: dict[type[Any], list[dict[str, Any]]] = {}
        self._pending = 0
        self.rows_written = 0

    def _t(self, seconds: float) -> datetime:
        return self.start + timedelta(seconds=seconds)

    def _add(self, model: type[Any], row: dict[str, Any]) -> None:
        self._buf.setdefault(model, []).append({"session_id": self.session_id, **row})
        self._pending += 1

    def _next(self) -> int:
        self._n += 1
        return self._n

    async def __call__(self, event: Event) -> None:
        if isinstance(event, TelemetryEvent):
            rx = event.ts if event.ts_rx is None else event.ts_rx
            self._add(
                Telemetry,
                dict(
                    ts=self._t(rx),
                    n=self._next(),
                    channel_id=event.channel,
                    ts_pkt=self._t(event.ts),
                    value=event.value,
                    cmd_mask=event.cmd_mask,
                    auth_ok=event.auth_ok,
                    synthetic=event.synthetic,
                ),
            )
        elif isinstance(event, PacketEvent):
            self._add(
                PacketRow,
                dict(
                    ts=self._t(event.ts_rx),
                    n=self._next(),
                    apid=event.apid,
                    seq_count=event.seq_count,
                    ts_pkt=self._t(event.ts_pkt),
                    kind="TC" if event.ptype else "TM",
                    auth_ok=event.auth_ok,
                    size=event.size,
                    synthetic=event.synthetic,
                ),
            )
        elif isinstance(event, MalformedFrameEvent):
            self._add(
                MalformedFrame,
                dict(
                    ts=self._t(event.ts_rx),
                    n=self._next(),
                    size=event.size,
                    reason=event.reason[:200],
                ),
            )
        elif isinstance(event, LinkEvent):
            self._add(
                LinkMetric,
                dict(
                    ts=self._t(event.ts),
                    snr_db=event.snr_db,
                    ber=event.ber,
                    latency_ms=event.latency_ms,
                    loss_pct=event.loss_pct,
                    rx_pps=event.rx_pps,
                    queue_depth=event.queue_depth,
                    synthetic=event.synthetic,
                ),
            )
        elif isinstance(event, CommandEvent):
            self._add(
                CommandLog,
                dict(
                    ts=self._t(event.ts),
                    opcode=event.opcode,
                    opcode_name=event.opcode_name[:64],
                    source=event.source[:64],
                    auth_ok=event.auth_ok,
                    in_contact_window=event.in_contact_window,
                    synthetic=event.synthetic,
                ),
            )
        elif isinstance(event, AuthEvent):
            self._add(
                AuthEventRow,
                dict(
                    ts=self._t(event.ts),
                    source=event.source[:128],
                    success=event.success,
                    method=event.method[:32],
                    synthetic=event.synthetic,
                ),
            )
        if self._pending >= self.batch_size:
            await self.flush()

    async def flush(self) -> None:
        if not self._pending:
            return
        buf, self._buf, count = self._buf, {}, self._pending
        self._pending = 0
        async with self.factory() as s:
            for model, rows in buf.items():
                await s.execute(insert(model), rows)
            await s.commit()
        self.rows_written += count
