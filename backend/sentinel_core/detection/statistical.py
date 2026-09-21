"""L1: statistical detectors on individual telemetry channels.

Design choice (docs/ARCHITECTURE.md 6.2, ADR 0010): alarm thresholds are *empirical*. During
calibration each statistic's distribution is measured on nominal data and the alarm level is set to
``margin x`` the largest value seen. That adapts automatically to the structure Phase 1 found in
the real data (bimodal payload bursts, orbital cycles, constant channels, test values outside the
training range) instead of assuming Gaussian noise.

A channel may have a seasonal baseline (``period`` steps): the statistics then run on the residual
from a per-phase median.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import pairwise

import numpy as np

from sentinel_core.events import Event, EventKind, TelemetryEvent
from sentinel_core.timebase import STEP_SECONDS

from .base import Detector, DetectorOutput, Evidence, Layer, now
from .protocol import ev

MIN_CAL_SAMPLES = 30
REF_ALPHA = 0.05  # memory of the slow reference used to detrend for CUSUM (about 20 steps)
GATE_Z = 3.0  # the reference does not adapt to samples further than this from it
MARGIN = 1.25  # alarm at this multiple of the largest calibration value
CUSUM_MARGIN = 1.5  # CUSUM excursions are random-walk-like: leave more headroom
CUSUM_FLOOR = 10.0  # decision interval h in sigma units (k = 0.5)
SHORT = 5  # samples in the short moving mean
WINDOW = 20  # samples in the variance window
FLAT_MIN_RUN = 8  # identical consecutive samples that count as "stuck"


def step_of(ts: float) -> int:
    return round(ts / STEP_SECONDS)


def _score(ratio: float) -> float:
    """Map (statistic / alarm level) to [0, 1]: 0.5 exactly at the alarm level, 1.0 at 2x."""
    return min(1.0, max(0.0, ratio / 2.0))


@dataclass(slots=True)
class ChannelModel:
    """Everything learned about one channel from nominal data."""

    channel: str
    period: int | None
    lo: float
    hi: float
    span: float
    phase_median: dict[int, float]
    global_median: float
    sigma: float  # residual scale (clipped std)
    sigma_d: float  # scale of the detrended residual (residual minus its slow reference)
    z1_max: float  # largest |r|/sigma in calibration
    zma_max: float  # largest |mean of last SHORT residuals|/sigma
    roc_max: float  # largest |r_t - r_(t-1)|/sigma
    cusum_max: float
    ratio_lo: float  # smallest windowed-std ratio
    ratio_hi: float  # largest windowed-std ratio
    max_flat_run: int
    constant: bool

    def baseline(self, step: int) -> float:
        if self.period is None:
            return self.global_median
        return self.phase_median.get(step % self.period, self.global_median)


def detrend(r: np.ndarray, sigma_guess: float) -> np.ndarray:
    """Residual minus a slow, outlier-gated EWMA reference (same recursion as the live detector)."""
    d = np.empty_like(r)
    ref = 0.0  # residuals are centered on the baseline, so the expected reference is zero
    for i, x in enumerate(r):
        d[i] = x - ref
        if abs(d[i]) < GATE_Z * sigma_guess:
            ref += REF_ALPHA * (x - ref)
    return d


def fit_channel(
    channel: str, steps: list[int], values: list[float], period: int | None
) -> ChannelModel:
    v = np.asarray(values, dtype=np.float64)
    ok = np.isfinite(v)
    if ok.sum() < MIN_CAL_SAMPLES:
        raise ValueError(f"{channel}: need >= {MIN_CAL_SAMPLES} finite calibration samples")
    st = np.asarray(steps)[ok]
    v = v[ok]
    lo, hi = float(v.min()), float(v.max())
    span = hi - lo
    gmed = float(np.median(v))
    phase_med: dict[int, float] = {}
    if period is not None:
        for ph in range(period):
            sel = v[(st % period) == ph]
            if len(sel) >= 2:
                phase_med[ph] = float(np.median(sel))
    base = np.array([phase_med.get(int(s) % period, gmed) if period else gmed for s in st])
    r = v - base
    q_lo, q_hi = np.percentile(r, [0.5, 99.5])
    sigma = float(np.std(np.clip(r, q_lo, q_hi)))
    constant = span == 0.0
    sigma = max(sigma, 1e-6 * max(abs(gmed), 1.0), 1e-9)

    z = r / sigma
    z1_max = float(np.abs(z).max())
    ma = np.convolve(z, np.ones(SHORT) / SHORT, mode="valid")
    zma_max = float(np.abs(ma).max()) if len(ma) else z1_max
    d = detrend(r, sigma_guess=sigma)
    dq_lo, dq_hi = np.percentile(d, [0.5, 99.5])
    sigma_d = max(float(np.std(np.clip(d, dq_lo, dq_hi))), 1e-9)
    zd = d / sigma_d
    roc_max = float(np.abs(np.diff(zd)).max()) if len(zd) > 1 else 0.0
    s_hi = s_lo = cusum_max = 0.0
    for zi in zd:
        s_hi = max(0.0, s_hi + zi - 0.5)
        s_lo = max(0.0, s_lo - zi - 0.5)
        cusum_max = max(cusum_max, s_hi, s_lo)
    ratios = [
        float(np.std(r[i - WINDOW : i]) / sigma)
        for i in range(WINDOW, len(r) + 1, 2)
        if len(r) >= WINDOW
    ]
    run = best = 1
    for a, b in pairwise(v):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return ChannelModel(
        channel=channel,
        period=period,
        lo=lo,
        hi=hi,
        span=span,
        phase_median=phase_med,
        global_median=gmed,
        sigma=sigma,
        sigma_d=sigma_d,
        z1_max=z1_max,
        zma_max=zma_max,
        roc_max=roc_max,
        cusum_max=cusum_max,
        ratio_lo=min(ratios) if ratios else 1.0,
        ratio_hi=max(ratios) if ratios else 1.0,
        max_flat_run=best,
        constant=constant,
    )


@dataclass(slots=True)
class _Runtime:
    resid: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    s_hi: float = 0.0
    s_lo: float = 0.0
    last_r: float | None = None
    ref: float = 0.0
    last_v: float | None = None
    flat_run: int = 1


class StatisticalDetector(Detector):
    """Range, residual z, CUSUM, rate-of-change, flatline, variance shift and dropout checks."""

    name = "l1.statistical"
    layer = Layer.L1
    consumes = frozenset({EventKind.TELEMETRY})

    def __init__(
        self, periods: Mapping[str, int | None] | None = None, margin: float = MARGIN
    ) -> None:
        self.periods = periods or {}
        self.margin = margin
        self._cal: dict[str, tuple[list[int], list[float]]] = {}
        self.models: dict[str, ChannelModel] = {}
        self._rt: dict[str, _Runtime] = {}

    # -- calibration ----------------------------------------------------------------
    def learn(self, event: Event) -> None:
        if isinstance(event, TelemetryEvent):
            steps, vals = self._cal.setdefault(event.channel, ([], []))
            steps.append(step_of(now(event)))
            vals.append(event.value)

    def freeze(self) -> None:
        for ch, (steps, vals) in self._cal.items():
            self.models[ch] = fit_channel(ch, steps, vals, self.periods.get(ch))
        self._cal.clear()

    def reset(self) -> None:
        self._rt.clear()

    # -- streaming ------------------------------------------------------------------
    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, TelemetryEvent):
            return []
        m = self.models.get(event.channel)
        if m is None:
            return []
        ts = now(event)
        v = event.value
        rt = self._rt.setdefault(event.channel, _Runtime())
        ch = event.channel
        out: list[DetectorOutput] = []

        if not math.isfinite(v):
            return self._out(
                ts,
                1.0,
                "The channel reported no valid value (dropout).",
                (ev("value_finite", False, True),),
                ch,
                "dropout",
            )

        r = v - m.baseline(step_of(ts))
        z = r / m.sigma
        rt.resid.append(r)

        # 1. range against the calibrated envelope (plus a margin proportional to the span)
        pad = 0.1 * m.span + 1e-9
        excess = max(m.lo - pad - v, v - (m.hi + pad), 0.0)
        if excess > 0:
            score = min(1.0, 0.5 + excess / (2 * max(m.span, 1e-9)))
            out += self._out(
                ts,
                score,
                "The value is outside the range seen during calibration.",
                (ev("value", v, f"[{m.lo:.4g}, {m.hi:.4g}]"), ev("excess", excess)),
                ch,
                "range",
            )

        # 2. single-sample residual and short moving mean of the residual
        z1_thr = max(m.z1_max * self.margin, 4.0)
        out += self._stat_out(ts, ch, "zscore", abs(z), z1_thr, "residual", z)
        if len(rt.resid) >= SHORT:
            zma = float(np.mean(list(rt.resid)[-SHORT:])) / m.sigma
            out += self._stat_out(
                ts,
                ch,
                "level_shift",
                abs(zma),
                max(m.zma_max * self.margin, 3.0),
                "mean_residual",
                zma,
            )

        # 3. CUSUM (two-sided) on the detrended residual: catches abrupt persistent shifts while
        #    ignoring slow nuisance drift (slow drift within limits is L3's job, by design)
        dev = r - rt.ref
        if abs(dev) < GATE_Z * m.sigma:
            rt.ref += REF_ALPHA * (r - rt.ref)
        zd = dev / m.sigma_d
        rt.s_hi = max(0.0, rt.s_hi + zd - 0.5)
        rt.s_lo = max(0.0, rt.s_lo - zd - 0.5)
        cus = max(rt.s_hi, rt.s_lo)
        out += self._stat_out(
            ts, ch, "cusum", cus, max(m.cusum_max * CUSUM_MARGIN, CUSUM_FLOOR), "cusum", cus
        )

        # 4. rate of change of the residual
        if rt.last_r is not None:
            roc = abs(zd - rt.last_r)
            out += self._stat_out(
                ts, ch, "rate_of_change", roc, max(m.roc_max * self.margin, 6.0), "jump", roc
            )
        rt.last_r = zd

        # 5. stuck value (identical consecutive samples), unless the channel is constant by nature
        rt.flat_run = rt.flat_run + 1 if rt.last_v is not None and v == rt.last_v else 1
        rt.last_v = v
        if not m.constant and rt.flat_run >= max(FLAT_MIN_RUN, 2 * m.max_flat_run):
            run = (ev("identical_samples", float(rt.flat_run), float(m.max_flat_run)),)
            out += self._out(
                ts, 1.0, "The value has stopped changing (flatline).", run, ch, "flatline"
            )

        # 6. variance collapse or inflation over a sliding window
        if len(rt.resid) == WINDOW and not m.constant:
            ratio = float(np.std(rt.resid)) / m.sigma
            hi_thr = max(m.ratio_hi * self.margin, 1.5)
            lo_thr = m.ratio_lo / self.margin
            if ratio > hi_thr:
                out += self._out(
                    ts,
                    _score(ratio / hi_thr),
                    "The noise level is well above its calibrated level.",
                    (ev("noise_ratio", round(ratio, 2), round(hi_thr, 2)),),
                    ch,
                    "variance",
                )
            elif lo_thr > 0 and ratio < lo_thr * 0.5 and m.ratio_lo > 0.3:
                out += self._out(
                    ts,
                    _score(lo_thr / max(ratio, 1e-9) / 2),
                    "The noise level has collapsed.",
                    (ev("noise_ratio", round(ratio, 3), round(lo_thr, 3)),),
                    ch,
                    "variance",
                )
        return out

    def _stat_out(
        self, ts: float, ch: str, sub: str, stat: float, thr: float, label: str, signed: float
    ) -> list[DetectorOutput]:
        if thr <= 0:
            return []
        return self._out(
            ts,
            _score(stat / thr),
            f"The {sub.replace('_', ' ')} statistic exceeds its calibrated alarm level.",
            (
                ev(label, round(signed, 3), round(thr, 3)),
                Evidence(name="alarm_level_ratio", observed=round(stat / thr, 3)),
            ),
            ch,
            sub,
        )
