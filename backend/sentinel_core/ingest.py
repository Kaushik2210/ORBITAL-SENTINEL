"""Ingestion: raw frames and side-channel logs -> typed events, plus an async fan-out service.

The decoder is deliberately *forgiving about content and strict about reporting*: a frame with a
bad tag is still decoded (its values are flagged ``auth_ok=False``) so detectors can see tampering,
and malformed bytes become a :class:`MalformedFrameEvent` instead of an exception.
"""

from __future__ import annotations

import asyncio
import inspect
import struct
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .events import (
    AuthEvent,
    CommandEvent,
    Event,
    LinkEvent,
    MalformedFrameEvent,
    PacketEvent,
    TelemetryEvent,
)
from .packets import MalformedPacketError, decode
from .timebase import MISSION_EPOCH

_SAMPLE = struct.Struct(">f")
_SAMPLE_CMD = struct.Struct(">fQ")  # float32 value + uint64 multi-hot command bitmask


@dataclass(frozen=True, slots=True)
class ApidSpec:
    """The mission database entry for one APID: which channels its payload carries, in order."""

    apid: int
    name: str
    channels: tuple[str, ...]
    with_cmd: bool = False  # SMAP/MSL groups carry a command bitmask per channel
    synthetic: bool = False

    @property
    def sample_struct(self) -> struct.Struct:
        return _SAMPLE_CMD if self.with_cmd else _SAMPLE

    @property
    def payload_len(self) -> int:
        return len(self.channels) * self.sample_struct.size


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Bytes as received from the link, with the receive time (mission seconds)."""

    ts_rx: float
    data: bytes


@dataclass(frozen=True, slots=True)
class Tick:
    """Everything that happened in one simulation step, before decoding."""

    k: int
    ts: float
    frames: tuple[RawFrame, ...] = ()
    commands: tuple[CommandEvent, ...] = ()
    auths: tuple[AuthEvent, ...] = ()
    links: tuple[LinkEvent, ...] = ()
    # Ground truth of the physical state. Evaluator-only: detectors never receive it.
    truth: Mapping[str, float] = field(default_factory=dict)


class Ingestor:
    """Stateless decoder from raw bytes to events (state lives in the detectors)."""

    def __init__(self, apids: Mapping[int, ApidSpec], key: bytes, start: datetime) -> None:
        self.apids = dict(apids)
        self.key = key
        self._epoch_offset_s = (start - MISSION_EPOCH).total_seconds()

    def process_frame(self, frame: RawFrame) -> list[Event]:
        try:
            decoded = decode(frame.data, self.key)
        except MalformedPacketError as exc:
            return [MalformedFrameEvent(frame.ts_rx, len(frame.data), str(exc))]
        pkt = decoded.packet
        ts_pkt = pkt.timestamp_s - self._epoch_offset_s
        events: list[Event] = [
            PacketEvent(
                ts_rx=frame.ts_rx,
                apid=pkt.apid,
                seq_count=pkt.seq_count,
                ts_pkt=ts_pkt,
                ptype=pkt.ptype,
                auth_ok=decoded.auth_ok,
                size=len(frame.data),
            )
        ]
        spec = self.apids.get(pkt.apid)
        if spec is None:
            events.append(
                MalformedFrameEvent(frame.ts_rx, len(frame.data), f"unknown apid {pkt.apid}")
            )
            return events
        if len(pkt.payload) != spec.payload_len:
            events.append(
                MalformedFrameEvent(
                    frame.ts_rx,
                    len(frame.data),
                    f"apid {pkt.apid} payload {len(pkt.payload)} B, expected {spec.payload_len} B",
                )
            )
            return events
        sizes = spec.sample_struct
        for i, channel in enumerate(spec.channels):
            fields = sizes.unpack_from(pkt.payload, i * sizes.size)
            events.append(
                TelemetryEvent(
                    ts=ts_pkt,
                    channel=channel,
                    value=float(fields[0]),
                    cmd_mask=int(fields[1]) if spec.with_cmd else 0,
                    synthetic=spec.synthetic,
                    auth_ok=decoded.auth_ok,
                    ts_rx=frame.ts_rx,
                )
            )
        return events

    def process_tick(self, tick: Tick) -> list[Event]:
        events: list[Event] = []
        for frame in tick.frames:
            events.extend(self.process_frame(frame))
        events.extend(tick.commands)
        events.extend(tick.auths)
        events.extend(tick.links)
        return events


Sink = Callable[[Event], Awaitable[None] | None]


@dataclass(slots=True)
class IngestStats:
    ticks: int = 0
    frames: int = 0
    events: int = 0
    malformed: int = 0
    unauthenticated_frames: int = 0


class AsyncIngest:
    """Decouples a (possibly paced) tick source from its consumers with a bounded queue.

    The bounded queue gives backpressure: a slow sink slows the source instead of growing memory.
    Sinks run in registration order for every event, preserving per-source ordering.
    """

    def __init__(self, ingestor: Ingestor, sinks: Sequence[Sink], maxsize: int = 256) -> None:
        self.ingestor = ingestor
        self.sinks = list(sinks)
        self.stats = IngestStats()
        self._maxsize = maxsize

    async def run(self, source: AsyncIterator[Tick]) -> IngestStats:
        queue: asyncio.Queue[Tick | None] = asyncio.Queue(self._maxsize)

        async def produce() -> None:
            try:
                async for tick in source:
                    await queue.put(tick)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (tick := await queue.get()) is not None:
                await self._consume(tick)
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
        if not producer.cancelled() and (error := producer.exception()) is not None:
            raise error  # the tick source failed: surface it instead of ending "successfully"
        return self.stats

    async def _consume(self, tick: Tick) -> None:
        events = self.ingestor.process_tick(tick)
        self.stats.ticks += 1
        self.stats.frames += len(tick.frames)
        self.stats.events += len(events)
        for e in events:
            if isinstance(e, MalformedFrameEvent):
                self.stats.malformed += 1
            elif isinstance(e, PacketEvent) and not e.auth_ok:
                self.stats.unauthenticated_frames += 1
            for sink in self.sinks:
                result = sink(e)
                if inspect.isawaitable(result):
                    await result
