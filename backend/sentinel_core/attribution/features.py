"""Turn an incident window's detector evidence into a fixed numeric feature vector.

Every feature is computed from stored :class:`DetectorOutput` evidence and a tiny context, so a
decision can be recomputed exactly from what was stored (ADR 0006). Ground truth is never an input;
``weather_covered`` says whether we *have* space-weather data for the session, not what it contains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sentinel_core.detection.base import DetectorOutput, Layer
from sentinel_core.detection.engine import IncidentWindow
from sentinel_core.timebase import STEP_SECONDS

FEATURES: tuple[str, ...] = (
    # L4: protocol and security
    "seq_regression", "seq_gap", "stale_timestamp", "auth_tag_failed", "malformed_frames",
    "command_policy", "auth_anomaly", "link_shift", "rate_flood", "rate_drop",
    # L1: single-channel statistics
    "l1_range", "l1_zscore", "l1_level_shift", "l1_cusum", "l1_rate_of_change",
    "l1_flatline", "l1_variance", "l1_dropout", "l1_channels", "l1_spike_like",
    # L3: cross-channel and physics
    "l3_redundancy", "l3_sidedness", "l3_jump", "l3_slope", "l3_power_balance",
    # L5: environment
    "simultaneous_channels", "weather_overlap", "weather_particle", "weather_covered",
    # window shape and evidence strength
    "duration", "fired_count", "peak_score", "distinct_detectors", "layers_fired", "l1_steps",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class FeatureContext:
    weather_covered: bool


def _val(o: DetectorOutput, name: str) -> float | str | bool | None:
    for e in o.evidence:
        if e.name == name:
            return e.observed
    return None


def _num(x: float | str | bool | None, default: float = 0.0) -> float:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else default


def _max_score(outs: list[DetectorOutput], prefix: str) -> float:
    return max((o.score for o in outs if o.detector.startswith(prefix)), default=0.0)


def extract(window: IncidentWindow, ctx: FeatureContext) -> dict[str, float]:
    fired = window.fired
    allo = window.outputs
    f: dict[str, float] = dict.fromkeys(FEATURES, 0.0)

    seq = [o for o in fired if o.detector == "l4.sequence_integrity"]
    f["seq_regression"] = max(
        (o.score for o in seq if "backwards" in o.explanation or "seen again" in o.explanation),
        default=0.0,
    )
    f["seq_gap"] = max((o.score for o in seq if "missing" in o.explanation), default=0.0)
    f["stale_timestamp"] = _max_score(fired, "l4.timestamp_freshness")
    tag = [o for o in fired if o.detector == "l4.auth_tag_integrity"]
    f["auth_tag_failed"] = max((o.score for o in tag if _val(o, "auth_ok") is False), default=0.0)
    f["malformed_frames"] = max(
        (o.score for o in tag if _val(o, "reason") is not None), default=0.0
    )
    f["command_policy"] = _max_score(fired, "l4.command_policy")
    f["auth_anomaly"] = _max_score(fired, "l4.auth_anomaly")
    f["link_shift"] = _max_score(fired, "l4.link_shift")
    rate = [o for o in fired if o.detector == "l4.rate_anomaly"]
    f["rate_flood"] = max((o.score for o in rate if _val(o, "pattern") == "flood"), default=0.0)
    f["rate_drop"] = max((o.score for o in rate if _val(o, "pattern") == "drop"), default=0.0)

    for sub, key in (
        ("range", "l1_range"), ("zscore", "l1_zscore"), ("level_shift", "l1_level_shift"),
        ("cusum", "l1_cusum"), ("rate_of_change", "l1_rate_of_change"), ("flatline", "l1_flatline"),
        ("variance", "l1_variance"), ("dropout", "l1_dropout"),
    ):  # fmt: skip
        f[key] = _max_score(fired, f"l1.statistical.{sub}")
    l1 = [o for o in fired if o.layer is Layer.L1]
    f["l1_channels"] = math.log1p(len({o.channel for o in l1 if o.channel}))
    # how "glitch-like" (few steps, abrupt) versus sustained the L1 activity is
    steps = {round(o.ts / STEP_SECONDS) for o in l1}
    f["l1_spike_like"] = 1.0 if l1 and len(steps) <= 6 else 0.0

    l3 = [o for o in fired if o.detector.startswith("l3.redundant_sensors")]
    f["l3_redundancy"] = max((o.score for o in l3), default=0.0)
    if l3:
        f["l3_sidedness"] = sum(abs(_num(_val(o, "sidedness"))) for o in l3) / len(l3)
        f["l3_jump"] = min(1.0, max(_num(_val(o, "jump_z")) for o in l3) / 20.0)
        f["l3_slope"] = min(1.0, max(abs(_num(_val(o, "slope_z_per_step"))) for o in l3) / 0.5)
    f["l3_power_balance"] = _max_score(fired, "l3.power_balance")

    upset = [o for o in allo if o.detector == "l5.simultaneous_upset"]
    f["simultaneous_channels"] = (
        max((_num(_val(o, "channels_at_once")) for o in upset), default=0.0) / 5.0
    )
    wx = [o for o in allo if o.detector == "l5.weather_overlap"]
    f["weather_overlap"] = max((o.score for o in wx), default=0.0)
    f["weather_particle"] = 1.0 if any(_val(o, "event_kind") in ("SEP", "GST") for o in wx) else 0.0
    f["weather_covered"] = 1.0 if ctx.weather_covered else 0.0

    f["duration"] = min(1.0, (window.end - window.start) / STEP_SECONDS / 600.0)
    f["fired_count"] = min(1.0, math.log1p(len(fired)) / math.log1p(1000))
    # Weak, brief, single-detector windows are what spurious alarms look like; strong, broad,
    # multi-layer windows are what real incidents look like.
    f["peak_score"] = max((o.score for o in fired), default=0.0)
    f["distinct_detectors"] = min(1.0, len({o.detector for o in fired}) / 10.0)
    f["layers_fired"] = len({o.layer for o in fired}) / 5.0
    f["l1_steps"] = min(1.0, len(steps) / 100.0)
    return f


def vector(features: dict[str, float]) -> list[float]:
    return [features[name] for name in FEATURES]
