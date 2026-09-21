"""L5: environmental correlation. Second-stage detectors that read other detectors' outputs.

* :class:`SpaceWeatherOverlap` notes when a fired anomaly coincides with a real DONKI event
  (flare, solar energetic particle event, geomagnetic storm), keeping the event kind and size as
  evidence. It says nothing when there is no event; whether the *absence* of events is trustworthy
  is a separate fact (``weather_context``: the DONKI cache may simply not cover the session).
* :class:`SimultaneousUpset` flags the single-event-upset signature: several unrelated channels,
  in more than one subsystem, glitching at the same instant.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sentinel_core.events import Event, EventKind, WeatherEvent
from sentinel_core.timebase import STEP_SECONDS

from .base import Detector, DetectorOutput, Layer
from .protocol import ev

SPIKE_SUBS = frozenset({"zscore", "range", "rate_of_change", "level_shift", "dropout"})


class SpaceWeatherOverlap(Detector):
    name = "l5.weather_overlap"
    layer = Layer.L5
    consumes = frozenset({EventKind.SPACE_WEATHER})
    LEAD_S = 1800.0  # an effect can slightly precede the cataloged onset (timing uncertainty)
    LAG_S = 6 * 3600.0  # particle events keep affecting electronics for hours
    SUPPRESS_S = 1800.0  # at most one overlap report per event per half hour

    def __init__(self) -> None:
        self._events: list[WeatherEvent] = []
        self._last: dict[int, float] = {}

    def reset(self) -> None:
        self._events.clear()
        self._last.clear()

    def update(self, event: Event) -> list[DetectorOutput]:
        if isinstance(event, WeatherEvent):
            self._events.append(event)
        return []

    def observe(self, output: DetectorOutput) -> list[DetectorOutput]:
        if not output.fired or output.layer not in (Layer.L1, Layer.L3):
            return []
        hits = [
            (i, w)
            for i, w in enumerate(self._events)
            if w.begin_ts - self.LEAD_S <= output.ts <= w.end_ts + self.LAG_S
        ]
        out: list[DetectorOutput] = []
        for i, w in hits:
            if output.ts - self._last.get(i, -1e18) < self.SUPPRESS_S:
                continue
            self._last[i] = output.ts
            offset_h = (output.ts - w.begin_ts) / 3600.0
            particle = w.event_kind in ("SEP", "GST")
            score = 0.85 if particle else 0.6
            out += self._out(
                output.ts,
                score,
                f"An anomaly coincides with a real {w.event_kind} space-weather event.",
                (
                    ev("event_kind", w.event_kind),
                    ev("event_magnitude", w.magnitude if w.magnitude is not None else "n/a"),
                    ev("hours_after_onset", round(offset_h, 2), 0.0, unit="h"),
                    ev("anomaly_detector", output.detector),
                ),
                output.channel,
            )
        return out


class SimultaneousUpset(Detector):
    """Several channels in more than one subsystem glitching within the same step."""

    name = "l5.simultaneous_upset"
    layer = Layer.L5
    consumes = frozenset[EventKind]()  # observe-only: reads other detectors' outputs
    MIN_CHANNELS = 3

    def __init__(self, group_of: Callable[[str], str]) -> None:
        self.group_of = group_of
        self._by_step: dict[int, set[str]] = defaultdict(set)
        self._reported: dict[int, int] = {}

    def reset(self) -> None:
        self._by_step.clear()
        self._reported.clear()

    def update(self, event: Event) -> list[DetectorOutput]:
        return []

    def observe(self, output: DetectorOutput) -> list[DetectorOutput]:
        sub = output.detector.rsplit(".", 1)[-1]
        if (
            not output.fired
            or output.layer is not Layer.L1
            or sub not in SPIKE_SUBS
            or output.channel is None
        ):
            return []
        step = round(output.ts / STEP_SECONDS)
        chans = self._by_step[step]
        chans.add(output.channel)
        groups = {self.group_of(c) for c in chans}
        n = len(chans)
        if n < self.MIN_CHANNELS or len(groups) < 2 or self._reported.get(step, 0) >= n:
            return []
        self._reported[step] = n
        return self._out(
            output.ts,
            min(1.0, 0.4 + 0.15 * n),
            "Several channels in different subsystems glitched at the same instant.",
            (
                ev("channels_at_once", float(n), 1.0),
                ev("subsystems", float(len(groups)), 1.0),
                ev("channel_list", ",".join(sorted(chans))),
            ),
        )
