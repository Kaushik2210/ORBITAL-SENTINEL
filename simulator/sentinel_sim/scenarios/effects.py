"""Fault and attack effects, implemented as mission :class:`Injector` hooks.

Where an effect sits mirrors where the real fault or adversary sits:

* sensor level (``on_sensors``): frames are produced *after* the change, so they are authentic;
* link level (``on_frames``): frames are changed after signing; a keyless attacker breaks the tag,
  a keyed attacker (``rewrite``) re-signs;
* ground level (``on_side``): logs and link telemetry.

All randomness comes from named seeded streams, so a scenario is exactly reproducible.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from sentinel_core.events import AuthEvent, CommandEvent
from sentinel_core.ingest import RawFrame
from sentinel_core.packets import SpacePacket, decode, encode, unpack_floats
from sentinel_core.timebase import STEP_SECONDS, rng_for
from sentinel_sim.mission import Injector
from sentinel_sim.sidechannels import GROUND_STATIONS, OPCODES, OPERATORS, SideEvents

Params = dict[str, Any]


@dataclass(slots=True)
class Window:
    start: int
    end: int

    def active(self, k: int) -> bool:
        return self.start <= k < self.end

    def progress(self, k: int) -> float:
        return min(1.0, max(0.0, (k - self.start) / max(1, self.end - self.start)))


class Effect(Injector):
    """Common base: a window, params and a seeded RNG stream."""

    def __init__(
        self, win: Window, params: Params, seed: int, key: str, mission_key: bytes
    ) -> None:
        self.win, self.p, self.seed, self.key, self.mission_key = (
            win,
            params,
            seed,
            key,
            mission_key,
        )
        self.rng = rng_for(seed, key)

    def reset(self) -> None:
        self.rng = rng_for(self.seed, self.key)


# ---------------------------------------------------------------------------- sensor level


class SensorEffect(Effect):
    def __init__(
        self, kind: str, win: Window, params: Params, seed: int, key: str, mk: bytes
    ) -> None:
        super().__init__(win, params, seed, key, mk)
        self.kind = kind
        self.targets: list[str] = list(params["targets"])
        self._held: dict[str, float] = {}

    def reset(self) -> None:
        super().reset()
        self._held.clear()

    def on_sensors(
        self, k: int, values: dict[str, float], truth: dict[str, float]
    ) -> dict[str, float]:
        if not self.win.active(k):
            return values
        out = dict(values)
        prog = self.win.progress(k)
        for t in self.targets:
            v = values[t]
            if self.kind == "bias":
                out[t] = v + self.p["magnitude"]
            elif self.kind == "ramp":
                out[t] = v + self.p["magnitude"] * prog
            elif self.kind == "scale":
                out[t] = v * (1.0 + self.p["gain"])
            elif self.kind == "freeze":
                out[t] = self._held.setdefault(t, v)
            elif self.kind == "noise":
                out[t] = v + float(self.rng.normal(0, self.p["sigma"]))
            elif self.kind == "dropout":
                if self.rng.random() < self.p.get("prob", 1.0):
                    out[t] = float("nan")
            elif self.kind == "drift":  # malfunction: ramp that also gets noisier as it grows
                out[t] = (
                    v
                    + self.p["magnitude"] * prog
                    + float(self.rng.normal(0, self.p.get("noise_growth", 0.0) * prog))
                )
            elif self.kind == "spoof_clean":
                # The attacker reports the true quantity plus a slowly growing offset, *without* the
                # sensor's own noise: plausible, in-limits, but suspiciously clean.
                out[t] = truth[self.p["truth_key"]] + self.p["magnitude"] * prog
            elif self.kind == "spoof_baseline":
                # A plausible healthy level with realistic noise: hides a real change.
                out[t] = self.p["level"] * (
                    1.0 + float(self.rng.normal(0, self.p.get("noise", 0.03)))
                )
            else:  # pragma: no cover - guarded by the registry
                raise ValueError(self.kind)
        return out


def flip_float32_bit(value: float, bit: int) -> float:
    """Flip one bit of the IEEE-754 float32 representation of ``value``."""
    (raw,) = struct.unpack(">I", struct.pack(">f", value))
    (flipped,) = struct.unpack(">f", struct.pack(">I", raw ^ (1 << bit)))
    return float(flipped)


class SeuEffect(Effect):
    """Single-event upsets: random-bit float32 flips on several channels at the same instant."""

    def __init__(self, win: Window, params: Params, seed: int, key: str, mk: bytes) -> None:
        super().__init__(win, params, seed, key, mk)
        self.channels: list[str] = list(params["targets"])
        self.steps = set(params["steps"])
        self._plan: dict[tuple[int, str], int] = {}

    def _bit_for(self, k: int, ch: str, v: float) -> int:
        """Choose a bit (mantissa or exponent) whose flip stays finite and changes the value."""
        if (k, ch) not in self._plan:
            for _ in range(64):
                bit = int(self.rng.integers(0, 31))  # never the sign bit alone: keep it a "glitch"
                new = flip_float32_bit(v, bit)
                if math.isfinite(new) and abs(new - v) > 1e-3 * max(abs(v), 1e-3):
                    self._plan[(k, ch)] = bit
                    break
            else:
                self._plan[(k, ch)] = 22
        return self._plan[(k, ch)]

    def on_sensors(
        self, k: int, values: dict[str, float], truth: dict[str, float]
    ) -> dict[str, float]:
        if k not in self.steps:
            return values
        out = dict(values)
        for ch in self.channels:
            out[ch] = flip_float32_bit(values[ch], self._bit_for(k, ch, values[ch]))
        return out


# ---------------------------------------------------------------------------- link level


class ReplayEffect(Effect):
    """Re-sends previously recorded valid frames (stamped as received *now*).

    ``mode="append"`` adds them to the live traffic; ``"replace"`` suppresses live frames so the
    receiver sees only old ones. Recorded frames keep their old sequence counters and timestamps
    and remain correctly signed.
    """

    def __init__(self, win: Window, params: Params, seed: int, key: str, mk: bytes) -> None:
        super().__init__(win, params, seed, key, mk)
        self.src_start: int = params["source_start"]
        self._rec: dict[int, list[RawFrame]] = {}

    def reset(self) -> None:
        super().reset()
        self._rec.clear()

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        src_end = self.src_start + (self.win.end - self.win.start)
        if self.src_start <= k < src_end:
            self._rec[k] = list(frames)
        if not self.win.active(k):
            return frames
        old = self._rec.get(self.src_start + (k - self.win.start), [])
        stamped = [replace(f, ts_rx=k * STEP_SECONDS) for f in old]
        return stamped if self.p.get("mode", "append") == "replace" else [*frames, *stamped]


class TamperEffect(Effect):
    """Flips a payload byte after signing: a keyless man-in-the-middle."""

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        if not self.win.active(k):
            return frames
        out: list[RawFrame] = []
        for f in frames:
            raw = bytearray(f.data)
            raw[14 + int(self.rng.integers(0, max(1, len(raw) - 14 - 16)))] ^= 0x5A
            out.append(replace(f, data=bytes(raw)))
        return out


class RewriteEffect(Effect):
    """A keyed man-in-the-middle: rewrites a channel and re-signs, so frames stay authentic."""

    def __init__(self, win: Window, params: Params, seed: int, key: str, mk: bytes) -> None:
        super().__init__(win, params, seed, key, mk)
        self.apid: int = params["apid"]
        self.index: int = params["channel_index"]
        self.bias: float = params["magnitude"]

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        if not self.win.active(k):
            return frames
        out: list[RawFrame] = []
        for f in frames:
            d = decode(f.data, self.mission_key)
            if d.packet.apid != self.apid:
                out.append(f)
                continue
            vals = unpack_floats(d.packet.payload)
            vals[self.index] += (
                self.bias * self.win.progress(k) if self.p.get("ramp") else self.bias
            )
            payload = struct.pack(f">{len(vals)}f", *vals)
            pkt = SpacePacket(
                d.packet.apid, d.packet.seq_count, d.packet.ptype, d.packet.timestamp_us, payload
            )
            out.append(replace(f, data=encode(pkt, self.mission_key)))
        return out


class FloodEffect(Effect):
    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        if not self.win.active(k):
            return frames
        out = frames * int(self.p.get("copies", 20))
        n_bad = int(self.p.get("malformed", 0))
        junk = [
            RawFrame(
                k * STEP_SECONDS,
                bytes(self.rng.integers(0, 256, int(self.rng.integers(20, 60)), dtype=np.uint8)),
            )
            for _ in range(n_bad)
        ]
        return out + junk


class FrameDropEffect(Effect):
    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        if not self.win.active(k):
            return frames
        prob = float(self.p.get("prob", 1.0))
        return [f for f in frames if self.rng.random() >= prob]


# ---------------------------------------------------------------------------- ground level


class JammingEffect(Effect):
    """Uplink/downlink jamming: SNR falls, BER and loss rise, and some frames are lost."""

    def on_side(self, k: int, side: SideEvents) -> SideEvents:
        if not self.win.active(k) or side.link is None:
            return side
        snr = side.link.snr_db - float(self.p.get("snr_drop_db", 10.0))
        link = replace(
            side.link,
            snr_db=snr,
            ber=min(0.5, side.link.ber * 10 ** float(self.p.get("ber_decades", 4.0))),
            loss_pct=side.link.loss_pct + float(self.p.get("loss_pct", 12.0)),
            latency_ms=side.link.latency_ms + float(self.p.get("latency_ms", 40.0)),
        )
        return side.with_link(link)

    def on_frames(self, k: int, frames: list[RawFrame]) -> list[RawFrame]:
        p = float(self.p.get("frame_loss", 0.0))
        if not self.win.active(k) or p <= 0:
            return frames
        return [f for f in frames if self.rng.random() >= p]


class CommandInjection(Effect):
    """Telecommands the ground segment did not legitimately send."""

    VARIANTS = (
        "unauthorized_opcode",
        "out_of_window",
        "unknown_source",
        "unauthenticated",
        "whitelisted_anomalous",
    )

    def on_side(self, k: int, side: SideEvents) -> SideEvents:
        if not self.win.active(k) or self.rng.random() >= float(self.p.get("prob", 0.6)):
            return side
        ts = k * STEP_SECONDS
        v = self.p["variant"]
        name, code = "DUMP_MOMENTUM", OPCODES["DUMP_MOMENTUM"]
        station = GROUND_STATIONS[0]
        cmd = CommandEvent(ts, code, name, station, True, True)
        if v == "unauthorized_opcode":
            cmd = CommandEvent(ts, 0xEE, "PATCH_FLIGHT_SOFTWARE", station, True, True)
        elif v == "out_of_window":
            cmd = CommandEvent(ts, code, name, station, True, False)
        elif v == "unknown_source":
            cmd = CommandEvent(ts, code, name, "GS-UNREGISTERED", True, True)
        elif v == "unauthenticated":
            cmd = CommandEvent(ts, code, name, station, False, True)
        elif v == "whitelisted_anomalous":
            # everything looks legitimate except rate, and timing relative to the pass
            cmd = CommandEvent(ts, code, name, station, True, True)
            return replace(side, commands=(*side.commands, cmd, cmd, cmd))
        return replace(side, commands=(*side.commands, cmd))


class AuthAbuse(Effect):
    """Brute force, off-window login, and a login from an unknown account."""

    def on_side(self, k: int, side: SideEvents) -> SideEvents:
        if not self.win.active(k):
            return side
        ts = k * STEP_SECONDS
        v = self.p["variant"]
        if v == "bruteforce":
            n = int(self.p.get("attempts", 4))
            evs = tuple(AuthEvent(ts, "root@198.51.100.7", False, "password") for _ in range(n))
        elif v == "offwindow":
            evs = (AuthEvent(ts, OPERATORS[1], True, "password"),) if k == self.win.start else ()
        elif v == "new_source":
            evs = (
                (AuthEvent(ts, "svc-backup@203.0.113.20", True, "password"),)
                if k == self.win.start
                else ()
            )
        else:  # pragma: no cover - guarded by the registry
            raise ValueError(v)
        return replace(side, auths=(*side.auths, *evs))


# ---------------------------------------------------------------------------- registry

Builder = Callable[[Window, Params, int, str, bytes], Injector]

SENSOR_KINDS = (
    "bias", "ramp", "scale", "freeze", "noise", "dropout", "drift", "spoof_clean", "spoof_baseline",
)  # fmt: skip


def _sensor(kind: str) -> Builder:
    return lambda w, p, s, k, mk: SensorEffect(kind, w, p, s, k, mk)


REGISTRY: dict[str, Builder] = {
    **{kind: _sensor(kind) for kind in SENSOR_KINDS},
    "seu": lambda w, p, s, k, mk: SeuEffect(w, p, s, k, mk),
    "replay": lambda w, p, s, k, mk: ReplayEffect(w, p, s, k, mk),
    "tamper": lambda w, p, s, k, mk: TamperEffect(w, p, s, k, mk),
    "rewrite": lambda w, p, s, k, mk: RewriteEffect(w, p, s, k, mk),
    "flood": lambda w, p, s, k, mk: FloodEffect(w, p, s, k, mk),
    "frame_drop": lambda w, p, s, k, mk: FrameDropEffect(w, p, s, k, mk),
    "jamming": lambda w, p, s, k, mk: JammingEffect(w, p, s, k, mk),
    "command_injection": lambda w, p, s, k, mk: CommandInjection(w, p, s, k, mk),
    "auth_abuse": lambda w, p, s, k, mk: AuthAbuse(w, p, s, k, mk),
}

REQUIRED: dict[str, tuple[str, ...]] = {
    **{k: ("targets",) for k in SENSOR_KINDS},
    "bias": ("targets", "magnitude"),
    "ramp": ("targets", "magnitude"),
    "scale": ("targets", "gain"),
    "noise": ("targets", "sigma"),
    "drift": ("targets", "magnitude"),
    "spoof_clean": ("targets", "magnitude", "truth_key"),
    "spoof_baseline": ("targets", "level"),
    "seu": ("targets", "steps"),
    "replay": ("source_start",),
    "rewrite": ("apid", "channel_index", "magnitude"),
    "command_injection": ("variant",),
    "auth_abuse": ("variant",),
}


@dataclass(slots=True)
class AgingPlan:
    """Time-compressed real degradation: progress rises from ``p0`` to ``p1`` across a window."""

    battery: tuple[int, int, float, float] | None = None  # start, end, p0, p1
    wheel: tuple[int, int, float, float] | None = None
    base: tuple[float, float] = (0.05, 0.05)
    _cache: dict[int, tuple[float, float]] = field(default_factory=dict)

    def __call__(self, k: int) -> tuple[float, float]:
        def prog(spec: tuple[int, int, float, float] | None, base: float) -> float:
            if spec is None:
                return base
            s, e, p0, p1 = spec
            f = min(1.0, max(0.0, (k - s) / max(1, e - s)))
            return p0 + (p1 - p0) * f if k >= s else base

        return prog(self.battery, self.base[0]), prog(self.wheel, self.base[1])
