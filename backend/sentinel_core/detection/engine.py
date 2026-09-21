"""Event routing and incident grouping."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sentinel_core.events import Event, EventKind

from .base import Detector, DetectorOutput


class DetectionEngine:
    """Fans events out to the detectors that consume them.

    Calibration mirrors mission commissioning: feed a nominal stream through :meth:`calibrate`
    (detectors ``learn``), which then freezes every baseline before live processing starts.
    """

    def __init__(self, detectors: Sequence[Detector], inhibit_s: float = 0.0) -> None:
        """``inhibit_s`` silences all detectors for that many mission seconds (start-up transient).

        Baselines still update during the inhibit period; only alarms are suppressed.
        """
        for d in detectors:
            d.inhibit_until = inhibit_s
        names = [d.name for d in detectors]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate detector names: {names}")
        self.detectors = list(detectors)
        self._by_kind: dict[EventKind, list[Detector]] = defaultdict(list)
        for d in self.detectors:
            for kind in d.consumes:
                self._by_kind[kind].append(d)
        self._observers = [d for d in self.detectors if type(d).observe is not Detector.observe]
        self._frozen = False

    def calibrate(self, events: Iterable[Event]) -> None:
        if self._frozen:
            raise RuntimeError("engine is already calibrated")
        for e in events:
            for d in self._by_kind.get(e.kind, ()):
                d.learn(e)
        for d in self.detectors:
            d.freeze()
        self._frozen = True

    def process(self, event: Event) -> list[DetectorOutput]:
        if not self._frozen:
            raise RuntimeError("calibrate() must be called before process()")
        out: list[DetectorOutput] = []
        for d in self._by_kind.get(event.kind, ()):
            out.extend(d.update(event))
        return out + self._stage2(out)

    def flush(self) -> list[DetectorOutput]:
        """Drain detectors that buffer (e.g. cross-channel step assembly) at end of stream."""
        out: list[DetectorOutput] = []
        for d in self.detectors:
            out.extend(d.flush())
        return out + self._stage2(out)

    def _stage2(self, outputs: list[DetectorOutput]) -> list[DetectorOutput]:
        extra: list[DetectorOutput] = []
        for o in outputs:
            for d in self._observers:
                extra.extend(d.observe(o))
        return extra

    def reset(self) -> None:
        for d in self.detectors:
            d.reset()


@dataclass(slots=True)
class IncidentWindow:
    """A group of related detector outputs. ``outputs`` includes sub-threshold context."""

    id: int
    start: float
    end: float  # time of the latest *fired* output
    outputs: list[DetectorOutput] = field(default_factory=list)
    closed: bool = False

    @property
    def fired(self) -> list[DetectorOutput]:
        return [o for o in self.outputs if o.fired]

    @property
    def channels(self) -> set[str]:
        return {o.channel for o in self.outputs if o.channel}


class IncidentBuilder:
    """Opens an incident on the first fired output and closes it after ``quiet_s`` without one.

    Sub-threshold outputs (score >= emit floor) from the last ``preroll_s`` seconds are kept and
    attached when an incident opens, so early weak evidence is not lost.
    """

    def __init__(self, quiet_s: float = 900.0, preroll_s: float = 600.0) -> None:
        self.quiet_s = quiet_s
        self.preroll_s = preroll_s
        self._preroll: deque[DetectorOutput] = deque()
        self._open: IncidentWindow | None = None
        self._next_id = 1

    def add(self, out: DetectorOutput) -> None:
        if self._open is not None:
            self._open.outputs.append(out)
            if out.fired:
                self._open.end = max(self._open.end, out.ts)
            return
        if out.fired:
            start = min([out.ts, *(p.ts for p in self._preroll)])
            self._open = IncidentWindow(self._next_id, start, out.ts, [*self._preroll, out])
            self._next_id += 1
            self._preroll.clear()
        else:
            self._preroll.append(out)

    def advance(self, now_ts: float) -> list[IncidentWindow]:
        """Close and return the open incident if it has been quiet long enough."""
        while self._preroll and now_ts - self._preroll[0].ts > self.preroll_s:
            self._preroll.popleft()
        w = self._open
        if w is not None and now_ts - w.end > self.quiet_s:
            w.closed = True
            self._open = None
            return [w]
        return []

    def flush(self) -> list[IncidentWindow]:
        w, self._open = self._open, None
        if w is None:
            return []
        w.closed = True
        return [w]

    @property
    def open_window(self) -> IncidentWindow | None:
        return self._open
