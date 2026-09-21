"""L3: cross-channel and physics consistency (synthetic physics bus only; ADR 0004).

Two invariants the bus obeys, and that single-channel detectors cannot see:

* **Redundant sensors** measure the same quantity: ``batt_v_a``/``batt_v_b`` (difference is a fixed
  calibration offset plus noise) and ``rw1_vib_a``/``rw1_vib_b`` (ratio is ~1).
* **Power balance**: ``batt_i == solar_i - load_i`` up to sensor noise.

Divergence outputs carry *shape* evidence (jump, slope, which side deviates) because the hard
question is not "do they disagree" but "how": a step, a ramp, or a change in noise character.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from sentinel_core.events import Event, EventKind, TelemetryEvent

from .base import Detector, DetectorOutput, Evidence, Layer, now
from .protocol import ev
from .statistical import (
    CUSUM_FLOOR,
    CUSUM_MARGIN,
    MARGIN,
    MIN_CAL_SAMPLES,
    SHORT,
    WINDOW,
    Z_CLIP,
    _score,
    step_of,
)

SHAPE_WINDOW = 10


class StepAssembler:
    """Collects telemetry into complete per-step snapshots keyed by *receive* step.

    A snapshot is emitted when the next step's first sample arrives (or on :meth:`flush`). Frames
    replayed with a current receive time therefore land in the current snapshot, which is exactly
    what makes replayed values show up as cross-channel inconsistency.
    """

    def __init__(self, needed: frozenset[str]) -> None:
        self.needed = needed
        self._cur: int | None = None
        self._buf: dict[str, float] = {}

    def add(self, event: TelemetryEvent) -> tuple[int, dict[str, float]] | None:
        if event.channel not in self.needed:
            return None
        key = step_of(now(event))
        done = None
        if self._cur is not None and key > self._cur:
            done = self._take()
        if self._cur is None or key > self._cur:
            self._cur = key
        self._buf[event.channel] = event.value
        return done

    def flush(self) -> tuple[int, dict[str, float]] | None:
        return self._take()

    def reset(self) -> None:
        self._cur, self._buf = None, {}

    def _take(self) -> tuple[int, dict[str, float]] | None:
        if self._cur is None:
            return None
        snap = (self._cur, self._buf)
        self._buf = {}
        return snap if self.needed <= snap[1].keys() else None


@dataclass(slots=True)
class _Stationary:
    """Monitor for a (nominally) stationary scalar: level, moving mean and CUSUM in sigma units."""

    mean: float
    sigma: float
    z1_max: float
    zma_max: float
    cusum_max: float
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    s_hi: float = 0.0
    s_lo: float = 0.0

    @classmethod
    def fit(cls, name: str, values: list[float]) -> _Stationary:
        v = np.asarray([x for x in values if math.isfinite(x)], dtype=np.float64)
        if len(v) < MIN_CAL_SAMPLES:
            raise ValueError(f"{name}: need >= {MIN_CAL_SAMPLES} finite calibration samples")
        lo, hi = np.percentile(v, [0.5, 99.5])
        mean = float(np.mean(np.clip(v, lo, hi)))
        sigma = max(float(np.std(np.clip(v, lo, hi))), 1e-9 * max(abs(mean), 1.0), 1e-12)
        z = np.clip((v - mean) / sigma, -Z_CLIP, Z_CLIP)
        ma = np.convolve(z, np.ones(SHORT) / SHORT, mode="valid")
        s_hi = s_lo = cus = 0.0
        for zi in z:
            s_hi, s_lo = max(0.0, s_hi + zi - 0.5), max(0.0, s_lo - zi - 0.5)
            cus = max(cus, s_hi, s_lo)
        return cls(mean, sigma, float(np.abs(z).max()), float(np.abs(ma).max()), cus)

    def update(self, x: float) -> tuple[float, float, float]:
        z = (x - self.mean) / self.sigma
        self.recent.append(z)
        zc = max(-Z_CLIP, min(Z_CLIP, z))
        self.s_hi = max(0.0, self.s_hi + zc - 0.5)
        self.s_lo = max(0.0, self.s_lo - zc - 0.5)
        zma = float(np.mean(list(self.recent)[-SHORT:]))
        return z, zma, max(self.s_hi, self.s_lo)

    def score(self, z: float, zma: float, cus: float, margin: float) -> tuple[float, str]:
        cands = {
            "level": _score(abs(z) / max(self.z1_max * margin, 4.0)),
            "moving_mean": _score(abs(zma) / max(self.zma_max * margin, 3.0)),
            "cusum": _score(cus / max(self.cusum_max * CUSUM_MARGIN, CUSUM_FLOOR)),
        }
        best = max(cands, key=lambda k: cands[k])
        return cands[best], best

    def reset(self) -> None:
        self.recent.clear()
        self.s_hi = self.s_lo = 0.0


class _JumpTracker:
    """Recent step-to-step jumps of one sensor, in units of its own calibrated jump scale.

    With only two redundant sensors, *which* one is wrong is identifiable when one jumps out of
    character with its own history (a step fault). For a slow ramp both look ordinary: the
    tracker then reports no preference, which is the honest answer.
    """

    def __init__(self, scale: float) -> None:
        self.scale = scale
        self.last: float | None = None
        self.jumps: deque[float] = deque(maxlen=SHAPE_WINDOW)

    def update(self, x: float) -> None:
        if self.last is not None:
            self.jumps.append(abs(x - self.last) / self.scale)
        self.last = x

    def magnitude(self) -> float:
        return max(self.jumps, default=0.0)


def _jump_scale(values: list[float]) -> float:
    """Typical step-to-step change of a sensor (robust: clipped std of first differences)."""
    d = np.diff(np.asarray([v for v in values if math.isfinite(v)], dtype=np.float64))
    if len(d) < MIN_CAL_SAMPLES - 1:
        raise ValueError(f"need >= {MIN_CAL_SAMPLES} finite calibration samples for jump scale")
    lo, hi = np.percentile(d, [1, 99])
    return max(float(np.std(np.clip(d, lo, hi))), 1e-12)


@dataclass(frozen=True, slots=True)
class PairSpec:
    a: str
    b: str
    kind: str  # "diff": b - a is ~constant; "ratio": b / a is ~1
    label: str

    def combine(self, a: float, b: float) -> float:
        if self.kind == "ratio":
            return b / a if a != 0 else float("nan")
        return b - a


DEFAULT_PAIRS = (
    PairSpec("batt_v_a", "batt_v_b", "diff", "battery voltage"),
    PairSpec("rw1_vib_a", "rw1_vib_b", "ratio", "reaction-wheel vibration"),
)


class RedundantSensors(Detector):
    """Disagreement between redundant sensors, with shape and side-of-fault evidence."""

    name = "l3.redundant_sensors"
    layer = Layer.L3
    consumes = frozenset({EventKind.TELEMETRY})

    def __init__(self, pairs: tuple[PairSpec, ...] = DEFAULT_PAIRS, margin: float = MARGIN) -> None:
        self.pairs = pairs
        self.margin = margin
        needed = frozenset(c for p in pairs for c in (p.a, p.b))
        self._asm = StepAssembler(needed)
        self._cal: dict[str, list[float]] = {p.label: [] for p in pairs}
        self._mon: dict[str, _Stationary] = {}
        self._raw: dict[str, tuple[list[float], list[float]]] = {p.label: ([], []) for p in pairs}
        self._scales: dict[str, tuple[float, float]] = {}
        self._trk: dict[str, tuple[_JumpTracker, _JumpTracker]] = {}
        self._hist: dict[str, deque[float]] = {p.label: deque(maxlen=SHAPE_WINDOW) for p in pairs}

    # -- calibration ------------------------------------------------------------------
    def learn(self, event: Event) -> None:
        if isinstance(event, TelemetryEvent) and (snap := self._asm.add(event)):
            self._collect(snap[1])

    def _collect(self, vals: dict[str, float]) -> None:
        for p in self.pairs:
            self._cal[p.label].append(p.combine(vals[p.a], vals[p.b]))
            self._raw[p.label][0].append(vals[p.a])
            self._raw[p.label][1].append(vals[p.b])

    def freeze(self) -> None:
        if (snap := self._asm.flush()) is not None:
            self._collect(snap[1])
        for p in self.pairs:
            self._mon[p.label] = _Stationary.fit(p.label, self._cal[p.label])
            raw_a, raw_b = self._raw[p.label]
            self._scales[p.label] = (_jump_scale(raw_a), _jump_scale(raw_b))
        self._raw.clear()
        self._trk = {p.label: self._new_trackers(p.label) for p in self.pairs}
        self._asm.reset()

    def _new_trackers(self, label: str) -> tuple[_JumpTracker, _JumpTracker]:
        sa, sb = self._scales[label]
        return _JumpTracker(sa), _JumpTracker(sb)

    def reset(self) -> None:
        self._asm.reset()
        for m in self._mon.values():
            m.reset()
        for h in self._hist.values():
            h.clear()
        self._trk = {p.label: self._new_trackers(p.label) for p in self.pairs}

    # -- streaming --------------------------------------------------------------------
    def update(self, event: Event) -> list[DetectorOutput]:
        if isinstance(event, TelemetryEvent) and (snap := self._asm.add(event)):
            return self._evaluate(*snap)
        return []

    def flush(self) -> list[DetectorOutput]:
        snap = self._asm.flush()
        return self._evaluate(*snap) if snap else []

    def _evaluate(self, step: int, vals: dict[str, float]) -> list[DetectorOutput]:
        out: list[DetectorOutput] = []
        ts = step * 60.0
        for p in self.pairs:
            a, b = vals[p.a], vals[p.b]
            ta, tb = self._trk[p.label]
            if math.isfinite(a):
                ta.update(a)
            if math.isfinite(b):
                tb.update(b)
            d = p.combine(a, b)
            if not math.isfinite(d):
                out += self._out(
                    ts,
                    1.0,
                    f"A redundant {p.label} sensor reported no valid value.",
                    (ev("pair_finite", False, True),),
                    None,
                    p.label.replace(" ", "_"),
                )
                continue
            mon = self._mon[p.label]
            z, zma, cus = mon.update(d)
            hist = self._hist[p.label]
            hist.append(z)
            score, stat = mon.score(z, zma, cus, self.margin)
            if score < self.emit_floor:
                continue
            ma, mb = ta.magnitude(), tb.magnitude()
            side = (ma - mb) / (ma + mb + 1e-9)  # >0: sensor a jumped out of character more
            zs = np.array(hist)
            slope = float(np.polyfit(np.arange(len(zs)), zs, 1)[0]) if len(zs) >= 4 else 0.0
            jump = float(abs(zs[-1] - np.median(zs[:-3]))) if len(zs) >= 6 else 0.0
            out += self._out(
                ts,
                score,
                f"Redundant {p.label} sensors disagree ({stat}).",
                (
                    ev("divergence_z", round(z, 2), 0.0),
                    ev("trigger", stat),
                    Evidence(
                        name="sidedness", observed=round(side, 3), note="+1: first sensor jumped"
                    ),
                    ev("slope_z_per_step", round(slope, 3), 0.0),
                    ev("jump_z", round(jump, 2), 0.0),
                    ev("pair", p.label),
                ),
                None,
                p.label.replace(" ", "_"),
            )
        return out


class PowerBalance(Detector):
    """``batt_i - (solar_i - load_i)`` must be ~0: a lying current sensor or a hidden load."""

    name = "l3.power_balance"
    layer = Layer.L3
    consumes = frozenset({EventKind.TELEMETRY})
    NEEDED = frozenset({"batt_i", "solar_i", "load_i"})

    def __init__(self, margin: float = MARGIN) -> None:
        self.margin = margin
        self._asm = StepAssembler(self.NEEDED)
        self._cal: list[float] = []
        self._mon: _Stationary | None = None

    @staticmethod
    def _residual(v: dict[str, float]) -> float:
        return v["batt_i"] - (v["solar_i"] - v["load_i"])

    def learn(self, event: Event) -> None:
        if isinstance(event, TelemetryEvent) and (snap := self._asm.add(event)):
            self._cal.append(self._residual(snap[1]))

    def freeze(self) -> None:
        if (snap := self._asm.flush()) is not None:
            self._cal.append(self._residual(snap[1]))
        self._mon = _Stationary.fit("power_balance", self._cal)
        self._asm.reset()

    def reset(self) -> None:
        self._asm.reset()
        if self._mon:
            self._mon.reset()

    def update(self, event: Event) -> list[DetectorOutput]:
        if isinstance(event, TelemetryEvent) and (snap := self._asm.add(event)):
            return self._evaluate(*snap)
        return []

    def flush(self) -> list[DetectorOutput]:
        snap = self._asm.flush()
        return self._evaluate(*snap) if snap else []

    def _evaluate(self, step: int, vals: dict[str, float]) -> list[DetectorOutput]:
        assert self._mon is not None
        r = self._residual(vals)
        if not math.isfinite(r):
            return []
        z, zma, cus = self._mon.update(r)
        score, stat = self._mon.score(z, zma, cus, self.margin)
        return self._out(
            step * 60.0,
            score,
            f"Battery, solar and load currents do not balance ({stat}).",
            (
                ev("residual_a", round(r, 3), round(self._mon.mean, 3), unit="A"),
                ev("z", round(z, 2), 0.0),
            ),
            None,
        )
