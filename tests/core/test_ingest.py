from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from sentinel_core import packets as p
from sentinel_core.events import (
    AuthEvent,
    Event,
    MalformedFrameEvent,
    PacketEvent,
    TelemetryEvent,
)
from sentinel_core.ingest import ApidSpec, AsyncIngest, Ingestor, RawFrame, Tick
from sentinel_core.timebase import wall_us

KEY = b"k"
START = datetime(2024, 5, 14, tzinfo=UTC)
PLAIN = ApidSpec(0x200, "eps", ("a", "b"), synthetic=True)
CMD = ApidSpec(0x100, "smap", ("P-1",), with_cmd=True)
APIDS = {PLAIN.apid: PLAIN, CMD.apid: CMD}


def frame(apid: int, payload: bytes, ts: float = 120.0, seq: int = 0, key: bytes = KEY) -> RawFrame:
    pkt = p.SpacePacket(apid, seq, p.PacketType.TELEMETRY, wall_us(START, ts), payload)
    return RawFrame(ts, p.encode(pkt, key))


def ingestor() -> Ingestor:
    return Ingestor(APIDS, KEY, START)


def test_plain_frame_yields_packet_event_and_one_telemetry_event_per_channel() -> None:
    events = ingestor().process_frame(frame(0x200, p.pack_floats([1.5, -2.0]), ts=120.0, seq=7))
    pkt, *tel = events
    assert isinstance(pkt, PacketEvent)
    assert (pkt.apid, pkt.seq_count, pkt.auth_ok) == (0x200, 7, True)
    assert pkt.ts_pkt == pytest.approx(120.0)  # mission time recovered from the wire timestamp
    assert [(t.channel, t.value) for t in tel if isinstance(t, TelemetryEvent)] == [
        ("a", 1.5),
        ("b", -2.0),
    ]
    assert all(t.synthetic for t in tel if isinstance(t, TelemetryEvent))


def test_command_bitmask_survives_the_wire_multi_hot() -> None:
    import struct

    mask = 0b1010_0000_0000_0000_0000_0101  # several commands active at once
    payload = struct.pack(">fQ", 0.25, mask)
    events = ingestor().process_frame(frame(0x100, payload))
    (tel,) = [e for e in events if isinstance(e, TelemetryEvent)]
    assert tel.cmd_mask == mask
    assert tel.value == 0.25
    assert not tel.synthetic


def test_bad_tag_is_flagged_not_dropped() -> None:
    events = ingestor().process_frame(frame(0x200, p.pack_floats([1.0, 2.0]), key=b"attacker"))
    pkt = events[0]
    assert isinstance(pkt, PacketEvent)
    assert not pkt.auth_ok
    tel = [e for e in events if isinstance(e, TelemetryEvent)]
    assert len(tel) == 2
    assert all(not t.auth_ok for t in tel)


def test_malformed_bytes_become_an_event_not_an_exception() -> None:
    (e,) = ingestor().process_frame(RawFrame(5.0, b"\x00\x01\x02"))
    assert isinstance(e, MalformedFrameEvent)
    assert e.ts_rx == 5.0


def test_unknown_apid_is_reported() -> None:
    events = ingestor().process_frame(frame(0x7FF, b"\x00\x00\x00\x00"))
    assert isinstance(events[0], PacketEvent)
    assert isinstance(events[1], MalformedFrameEvent)
    assert "unknown apid" in events[1].reason
    assert not any(isinstance(e, TelemetryEvent) for e in events)


def test_wrong_payload_length_is_reported_and_yields_no_telemetry() -> None:
    events = ingestor().process_frame(frame(0x200, p.pack_floats([1.0])))  # needs 2 floats
    assert isinstance(events[1], MalformedFrameEvent)
    assert "expected 8 B" in events[1].reason
    assert not any(isinstance(e, TelemetryEvent) for e in events)


def test_process_tick_appends_side_channel_events() -> None:
    auth = AuthEvent(1.0, "op.x", False)
    events = ingestor().process_tick(
        Tick(0, 0.0, (frame(0x200, p.pack_floats([0, 0])),), auths=(auth,))
    )
    assert events[-1] is auth


async def source(ticks: list[Tick], fail_after: int | None = None) -> AsyncIterator[Tick]:
    for i, t in enumerate(ticks):
        if fail_after is not None and i == fail_after:
            raise RuntimeError("source exploded")
        yield t


def make_ticks(n: int) -> list[Tick]:
    return [
        Tick(k, k * 60.0, (frame(0x200, p.pack_floats([k, k]), ts=k * 60.0, seq=k),))
        for k in range(n)
    ]


def test_async_ingest_delivers_all_events_in_order_and_counts() -> None:
    seen: list[Event] = []
    ing = AsyncIngest(ingestor(), [seen.append], maxsize=2)
    stats = asyncio.run(ing.run(source(make_ticks(20))))
    assert stats.ticks == 20
    assert stats.frames == 20
    assert stats.events == 20 * 3  # packet + 2 telemetry per tick
    seqs = [e.seq_count for e in seen if isinstance(e, PacketEvent)]
    assert seqs == list(range(20))


def test_async_sinks_are_awaited_and_run_in_registration_order() -> None:
    order: list[str] = []

    async def slow(_: Event) -> None:
        await asyncio.sleep(0)
        order.append("async")

    ing = AsyncIngest(ingestor(), [lambda _e: order.append("sync"), slow])
    asyncio.run(ing.run(source(make_ticks(1))))
    assert order[:2] == ["sync", "async"]
    assert len(order) == 6


def test_stats_count_malformed_and_unauthenticated() -> None:
    ticks = [Tick(0, 0.0, (RawFrame(0.0, b"junk"), frame(0x200, p.pack_floats([1, 2]), key=b"x")))]
    stats = asyncio.run(AsyncIngest(ingestor(), []).run(source(ticks)))
    assert stats.malformed == 1
    assert stats.unauthenticated_frames == 1


def test_source_failure_propagates_instead_of_ending_quietly() -> None:
    ing = AsyncIngest(ingestor(), [])
    with pytest.raises(RuntimeError, match="source exploded"):
        asyncio.run(ing.run(source(make_ticks(5), fail_after=2)))
    assert ing.stats.ticks == 2  # ticks before the failure were still processed


def test_sink_failure_propagates_and_stops_the_producer() -> None:
    def boom(_: Event) -> None:
        raise ValueError("sink exploded")

    with pytest.raises(ValueError, match="sink exploded"):
        asyncio.run(AsyncIngest(ingestor(), [boom], maxsize=1).run(source(make_ticks(50))))
