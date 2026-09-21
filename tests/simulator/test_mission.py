from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sentinel_core.events import LinkEvent, MalformedFrameEvent, PacketEvent, TelemetryEvent
from sentinel_core.ingest import Ingestor, RawFrame, Tick
from sentinel_core.packets import seq_delta
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import bus, pcoe
from sentinel_sim.mission import (
    APID_ADCS,
    APID_EPS,
    DEFAULT_FRAME_KEY,
    Injector,
    Mission,
    MissionConfig,
    frame_key_from_env,
)
from sentinel_sim.sidechannels import SideEvents

REAL_ROOT = Path("data/raw")
needs_real = pytest.mark.skipif(
    not (REAL_ROOT / "smap_msl" / "labeled_anomalies.csv").is_file(),
    reason="real SMAP/MSL data not downloaded",
)


def make(seed: int = 1, injectors: tuple[Injector, ...] = (), **kw: object) -> Mission:
    return Mission(
        MissionConfig(seed=seed, **kw),  # type: ignore[arg-type]
        injectors=injectors,
        battery=pcoe.parametric_battery(),
        wheel=pcoe.parametric_wheel(),
    )


def ingest(m: Mission) -> Ingestor:
    return Ingestor(m.apids, m.config.frame_key, m.config.start)


def run(m: Mission, n: int) -> list[Tick]:
    return [m.tick(k) for k in range(n)]


def test_same_seed_gives_identical_wire_bytes_and_seed_changes_them() -> None:
    a = [f.data for t in run(make(1), 40) for f in t.frames]
    b = [f.data for t in run(make(1), 40) for f in t.frames]
    c = [f.data for t in run(make(2), 40) for f in t.frames]
    assert a == b
    assert a != c


def test_nominal_stream_is_authentic_sequential_and_complete() -> None:
    m = make()
    ing = ingest(m)
    last: dict[int, int] = {}
    channels: set[str] = set()
    for t in run(m, 100):
        assert len(t.frames) == 2  # EPS + ADCS
        for e in ing.process_tick(t):
            if isinstance(e, PacketEvent):
                assert e.auth_ok
                if e.apid in last:
                    assert seq_delta(last[e.apid], e.seq_count) == 1
                last[e.apid] = e.seq_count
                assert e.ts_pkt == pytest.approx(t.ts)
            elif isinstance(e, TelemetryEvent):
                assert e.synthetic
                channels.add(e.channel)
    assert channels == set(bus.BUS_CHANNELS)
    assert set(last) == {APID_EPS, APID_ADCS}


def test_sequence_counter_wraps_without_looking_like_a_regression() -> None:
    m = make()
    m._seq[APID_EPS] = 16383
    ing = ingest(m)
    seqs = [
        e.seq_count
        for t in run(m, 3)
        for e in ing.process_tick(t)
        if isinstance(e, PacketEvent) and e.apid == APID_EPS
    ]
    assert seqs == [16383, 0, 1]
    assert seq_delta(seqs[0], seqs[1]) == 1


def test_ticks_must_be_requested_in_order() -> None:
    m = make()
    m.tick(0)
    with pytest.raises(ValueError, match="sequential"):
        m.tick(5)


def test_seek_forward_and_backward_reproduce_the_same_ticks() -> None:
    ref = [t.frames[0].data for t in run(make(), 60)]
    m = make()
    m.seek(30)
    assert m.position == 30
    assert m.tick(30).frames[0].data == ref[30]
    m.seek(10)  # backwards: forces a reset and re-simulation
    assert m.tick(10).frames[0].data == ref[10]
    with pytest.raises(ValueError, match=">= 0"):
        m.seek(-1)


def test_link_event_reports_frame_rate_from_actual_frames() -> None:
    (link,) = make().tick(0).links
    assert link.rx_pps == pytest.approx(2 / STEP_SECONDS)
    assert link.queue_depth >= 0


class Bias(Injector):
    def on_sensors(
        self, k: int, values: dict[str, float], truth: dict[str, float]
    ) -> dict[str, float]:
        return {**values, "batt_v_a": values["batt_v_a"] + 2.0} if k >= 5 else values


def test_sensor_injection_changes_values_but_frames_stay_authentic() -> None:
    clean, dirty = make(), make(injectors=(Bias(),))
    ing = ingest(dirty)

    def batt_v_a(m: Mission, k: int, i: Ingestor) -> tuple[float, bool]:
        events = i.process_tick(m.tick(k))
        tel = next(e for e in events if isinstance(e, TelemetryEvent) and e.channel == "batt_v_a")
        return tel.value, tel.auth_ok

    for k in range(6):
        vc, _ = batt_v_a(clean, k, ingest(clean))
        vd, ok = batt_v_a(dirty, k, ing)
        assert ok  # a sensor-level attacker produces frames the link cannot tell apart
        expected_shift = 2.0 if k >= 5 else 0.0
        assert vd - vc == pytest.approx(expected_shift, abs=1e-3)


class Tamper(Injector):
    """Flips one byte of the first frame at step 3 (``offset`` 16 = payload, 10 = timestamp)."""

    def __init__(self, offset: int = 16) -> None:
        self.offset = offset

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        if k != 3:
            return frames
        raw = bytearray(frames[0].data)
        raw[self.offset] ^= 0xFF
        return [replace(frames[0], data=bytes(raw)), *frames[1:]]


def test_link_tampering_is_visible_as_failed_authentication() -> None:
    m = make(injectors=(Tamper(),))
    ing = ingest(m)
    flags = [
        [e.auth_ok for e in ing.process_tick(t) if isinstance(e, PacketEvent)] for t in run(m, 5)
    ]
    assert flags[3] == [False, True]
    assert all(all(f) for i, f in enumerate(flags) if i != 3)


def test_corrupting_the_timestamp_field_makes_the_frame_malformed_not_merely_unauthenticated() -> (
    None
):
    m = make(injectors=(Tamper(offset=10),))
    ing = ingest(m)
    events = [e for t in run(m, 5) for e in ing.process_tick(t)]
    malformed = [e for e in events if isinstance(e, MalformedFrameEvent)]
    assert len(malformed) == 1
    assert "microseconds" in malformed[0].reason


class Flood(Injector):
    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        return frames * 100 if k == 2 else frames


def test_flood_raises_reported_rate_and_queue_depth() -> None:
    ticks = run(make(injectors=(Flood(),)), 4)
    quiet, flood = ticks[1].links[0], ticks[2].links[0]
    assert flood.rx_pps == pytest.approx(200 / STEP_SECONDS)
    assert flood.rx_pps > 50 * quiet.rx_pps
    assert flood.queue_depth >= quiet.queue_depth + 30


class Jam(Injector):
    def on_side(self, k: int, side: SideEvents) -> SideEvents:
        assert side.link is not None
        return side.with_link(replace(side.link, snr_db=2.0, ber=0.1)) if k >= 1 else side


def test_side_injection_reaches_the_link_event() -> None:
    ticks = run(make(injectors=(Jam(),)), 3)
    assert ticks[0].links[0].snr_db > 10
    jam: LinkEvent = ticks[2].links[0]
    assert (jam.snr_db, jam.ber) == (2.0, 0.1)


def test_injector_reset_is_called_on_seek_back() -> None:
    class Counting(Injector):
        resets = 0

        def reset(self) -> None:
            Counting.resets += 1

    m = make(injectors=(Counting(),))
    base = Counting.resets  # the constructor resets once
    m.seek(5)
    m.seek(2)
    assert Counting.resets == base + 1


def test_truth_is_carried_on_the_tick_but_not_on_the_wire() -> None:
    t = make().tick(0)
    assert "soc" in t.truth
    assert b"soc" not in b"".join(f.data for f in t.frames)


def test_catalog_flags_every_bus_channel_synthetic() -> None:
    cat = make().catalog()
    assert {c.id for c in cat} == set(bus.BUS_CHANNELS)
    assert all(c.synthetic and c.source == "synthetic" and c.unit for c in cat)


def test_frame_key_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRAME_HMAC_KEY", raising=False)
    assert frame_key_from_env() == DEFAULT_FRAME_KEY
    monkeypatch.setenv("FRAME_HMAC_KEY", "abc")
    assert frame_key_from_env() == b"abc"


@needs_real
def test_real_smap_msl_replay_matches_the_arrays_through_the_wire() -> None:
    m = make(smap_channels=("P-1", "M-1", "T-10"), include_bus=False, smap_offset=100)
    raw_p1 = np.load(REAL_ROOT / "smap_msl/test/P-1.npy")
    raw_m1 = np.load(REAL_ROOT / "smap_msl/test/M-1.npy")
    ing = ingest(m)
    got: dict[str, list[TelemetryEvent]] = {"P-1": [], "M-1": []}
    for t in run(m, 25):
        for e in ing.process_tick(t):
            if isinstance(e, TelemetryEvent) and e.channel in got:
                got[e.channel].append(e)
                assert not e.synthetic
    for ch, raw in (("P-1", raw_p1), ("M-1", raw_m1)):
        vals = np.array([e.value for e in got[ch]])
        assert np.allclose(vals, raw[100:125, 0].astype(np.float32), atol=1e-6)
    assert {c.id: c.source for c in m.catalog()} == {
        "P-1": "smap",
        "M-1": "msl",
        "T-10": "unlabeled",  # no label row: never claimed as SMAP or MSL
    }
    assert m.steps_available == 670 - 100  # limited by the shortest channel (T-10, 670 points)
    with pytest.raises(IndexError):
        m.seek(700)
