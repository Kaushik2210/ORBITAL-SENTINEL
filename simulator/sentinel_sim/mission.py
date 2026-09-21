"""The mission: real SMAP/MSL replay + synthetic physics bus + side channels, packetized.

``Mission.tick(k)`` produces one :class:`~sentinel_core.ingest.Tick` of wire-level data. Attacks and
faults are applied through :class:`Injector` hooks at three positions, mirroring where a real
adversary or fault sits:

* ``on_sensors``  -- physical sensors, *before* packetization (sensor faults, sensor spoofing);
* ``on_frames``   -- the link, *after* packetization (tampering, replay, flood, dropout);
* ``on_side``     -- ground logs and link telemetry (command injection, auth abuse, jamming).

Ground truth (the true physical state) rides along in ``Tick.truth`` for the evaluator only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from sentinel_core.events import LinkEvent
from sentinel_core.ingest import ApidSpec, RawFrame, Tick
from sentinel_core.packets import PacketType, SpacePacket, encode
from sentinel_core.timebase import DEFAULT_START, STEP_SECONDS, rng_for, wall_us

from . import bus as busmod
from . import smap_msl
from .pcoe import BatteryTrajectory, WheelTrajectory, load_battery, load_wheel
from .sidechannels import SideChannels, SideEvents, queue_depth

# A synthetic demo key so the project runs out of the box. Real deployments set FRAME_HMAC_KEY.
DEFAULT_FRAME_KEY = b"orbital-sentinel-synthetic-demo-frame-key"
APID_SMAP_BASE = 0x100
APID_EPS = 0x200
APID_ADCS = 0x201
SEQ_MODULO = 1 << 14


def frame_key_from_env() -> bytes:
    key = os.environ.get("FRAME_HMAC_KEY")
    return key.encode() if key else DEFAULT_FRAME_KEY


class Injector:
    """Base class for scenario hooks. Override only what the scenario needs; defaults are no-ops."""

    def reset(self) -> None:
        """Called when the mission restarts (seek/replay); drop any per-run state."""

    def on_sensors(
        self, k: int, values: dict[str, float], truth: dict[str, float]
    ) -> dict[str, float]:
        return values

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        return frames

    def on_side(self, k: int, side: SideEvents) -> SideEvents:
        return side


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One row of the channel catalog (DB `channels` table / API)."""

    id: str
    family: str  # smap | msl | eps | adcs
    group: str
    source: str  # smap | msl | synthetic
    unit: str | None
    synthetic: bool
    group_is_synthetic_grouping: bool  # SMAP/MSL groups are display-only prefix groupings


@dataclass(frozen=True, slots=True)
class MissionConfig:
    seed: int = 0
    start: datetime = DEFAULT_START
    frame_key: bytes = DEFAULT_FRAME_KEY
    smap_channels: tuple[str, ...] = ()
    include_bus: bool = True
    data_root: Path = Path("data/raw")
    smap_offset: int = 0  # first test-array index replayed at step 0


@dataclass(slots=True)
class Mission:
    config: MissionConfig
    injectors: tuple[Injector, ...] = ()
    aging: busmod.AgingFn = busmod.healthy_aging
    battery: BatteryTrajectory | None = None
    wheel: WheelTrajectory | None = None
    apids: dict[int, ApidSpec] = field(init=False)
    _series: dict[str, smap_msl.ChannelSeries] = field(init=False, default_factory=dict)
    _spacecraft: dict[str, str] = field(init=False, default_factory=dict)
    _bus: busmod.PhysicsBus | None = field(init=False, default=None)
    _side: SideChannels = field(init=False)
    _seq: dict[int, int] = field(init=False, default_factory=dict)
    _noise: np.random.Generator = field(init=False)
    _next_k: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        cfg = self.config
        labels = smap_msl.load_labels(cfg.data_root) if cfg.smap_channels else {}
        self._series = {
            ch: smap_msl.load_channel(cfg.data_root, ch, labels) for ch in cfg.smap_channels
        }
        self._spacecraft = {ch: (s.spacecraft or "unlabeled") for ch, s in self._series.items()}
        self.apids = {}
        groups = sorted({smap_msl.group_of(ch) for ch in cfg.smap_channels})
        for i, g in enumerate(groups):
            chans = tuple(ch for ch in cfg.smap_channels if smap_msl.group_of(ch) == g)
            spec = ApidSpec(APID_SMAP_BASE + i, f"smap-msl-{g}", chans, with_cmd=True)
            self.apids[spec.apid] = spec
        if cfg.include_bus:
            battery = self.battery or load_battery(cfg.data_root)
            wheel = self.wheel or load_wheel(cfg.data_root)
            self._bus = busmod.PhysicsBus(cfg.seed, battery, wheel, self.aging)
            self.apids[APID_EPS] = ApidSpec(APID_EPS, "eps", busmod.EPS_CHANNELS, synthetic=True)
            self.apids[APID_ADCS] = ApidSpec(
                APID_ADCS, "adcs", busmod.ADCS_CHANNELS, synthetic=True
            )
        self._side = SideChannels(cfg.seed)
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self._seq = dict.fromkeys(self.apids, 0)
        self._noise = rng_for(self.config.seed, "mission-link-queue")
        self._side.reset()
        if self._bus:
            self._bus.reset()
        for inj in self.injectors:
            inj.reset()
        self._next_k = 0

    def seek(self, k: int) -> None:
        """Position so the next :meth:`tick` is step ``k`` (state integrates, so this replays)."""
        if k < 0:
            raise ValueError("k must be >= 0")
        if k < self._next_k:
            self.reset()
        while self._next_k < k:
            self.tick(self._next_k)

    @property
    def position(self) -> int:
        """Index of the step the next :meth:`tick` call must request."""
        return self._next_k

    @property
    def steps_available(self) -> int | None:
        """How many steps the replayed real channels can supply (None if unbounded)."""
        if not self._series:
            return None
        return min(len(s.test) for s in self._series.values()) - self.config.smap_offset

    def catalog(self) -> list[ChannelSpec]:
        def real(ch: str) -> ChannelSpec:
            kind = {"SMAP": "smap", "MSL": "msl"}.get(self._spacecraft[ch], "unlabeled")
            return ChannelSpec(ch, kind, smap_msl.group_of(ch), kind, None, False, True)

        out = [real(ch) for ch in self._series]
        if self._bus:
            out += [
                ChannelSpec(c, "eps", "EPS", "synthetic", busmod.UNITS[c], True, False)
                for c in busmod.EPS_CHANNELS
            ]
            out += [
                ChannelSpec(c, "adcs", "ADCS", "synthetic", busmod.UNITS[c], True, False)
                for c in busmod.ADCS_CHANNELS
            ]
        return out

    # ------------------------------------------------------------------ one step
    def tick(self, k: int) -> Tick:
        if k != self._next_k:
            raise ValueError(f"steps must be sequential: expected {self._next_k}, got {k}")
        avail = self.steps_available
        if avail is not None and k >= avail:
            raise IndexError(f"step {k} beyond the {avail} replayable steps")
        ts = k * STEP_SECONDS
        values: dict[str, float] = {}
        truth: dict[str, float] = {}
        if self._bus:
            sample = self._bus.step(k)
            values.update(sample.values)
            truth.update(sample.truth)
        idx = self.config.smap_offset + k
        cmds = {ch: int(s.test_cmd[idx]) for ch, s in self._series.items()}
        for ch, s in self._series.items():
            values[ch] = float(s.test[idx])
        for inj in self.injectors:
            values = inj.on_sensors(k, values, truth)

        frames = self._packetize(k, ts, values, cmds)
        for inj in self.injectors:
            frames = inj.on_frames(k, frames)

        side = self._side.step(k)
        for inj in self.injectors:
            side = inj.on_side(k, side)
        links: tuple[LinkEvent, ...] = ()
        if side.link is not None:
            depth = queue_depth(float(self._noise.normal(1.5, 1.0)), len(frames), len(self.apids))
            links = (
                replace(
                    side.link,
                    rx_pps=len(frames) / STEP_SECONDS,
                    queue_depth=side.link.queue_depth + depth,
                ),
            )
        self._next_k = k + 1
        return Tick(k, ts, tuple(frames), side.commands, side.auths, links, truth)

    def _packetize(
        self, k: int, ts: float, values: dict[str, float], cmds: dict[str, int]
    ) -> list[RawFrame]:
        frames: list[RawFrame] = []
        ts_us = wall_us(self.config.start, ts)
        for apid, spec in self.apids.items():
            payload = bytearray()
            for ch in spec.channels:
                v = values.get(ch, float("nan"))  # an injector may drop a channel: NaN on the wire
                payload += spec.sample_struct.pack(*((v, cmds[ch]) if spec.with_cmd else (v,)))
            pkt = SpacePacket(apid, self._seq[apid], PacketType.TELEMETRY, ts_us, bytes(payload))
            self._seq[apid] = (self._seq[apid] + 1) % SEQ_MODULO
            frames.append(RawFrame(ts, encode(pkt, self.config.frame_key)))
        return frames
