"""Detector framework: streaming, stateful detectors that emit scored, explained outputs.

Contract (ARCHITECTURE.md section 6):

* a detector consumes typed events and returns zero or more :class:`DetectorOutput`;
* ``score`` is in [0, 1] and monotone in evidence strength; ``fired`` means ``score >= fire_at``;
* every output carries machine-readable :class:`Evidence` and a one-sentence explanation;
* detectors never see ground truth, and are deterministic for a given input order;
* nominal-behavior baselines are learned in a *calibration* pass (``learn`` then ``freeze``),
  the way a real mission commissions its monitors, so scenarios can be evaluated fairly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from sentinel_core.events import (
    Event,
    EventKind,
    MalformedFrameEvent,
    PacketEvent,
    TelemetryEvent,
)


def now(event: Event) -> float:
    """Detection time of an event: when it was *received*, not what a packet claims.

    A replayed frame carries an old packet timestamp; using it as "now" would make detections
    appear in the past and confuse incident windows.
    """
    if isinstance(event, TelemetryEvent):
        return event.ts if event.ts_rx is None else event.ts_rx
    if isinstance(event, PacketEvent | MalformedFrameEvent):
        return event.ts_rx
    return float(getattr(event, "ts", getattr(event, "begin_ts", 0.0)))


class Layer(StrEnum):
    L1 = "L1"  # statistical
    L2 = "L2"  # machine learning
    L3 = "L3"  # cross-channel / physics
    L4 = "L4"  # protocol / security
    L5 = "L5"  # environmental correlation


class Evidence(BaseModel):
    """One observation behind an output; ``observed``/``expected`` are what a human compares."""

    model_config = ConfigDict(frozen=True)

    name: str
    observed: float | str | bool | None = None
    expected: float | str | None = None
    unit: str | None = None
    note: str = ""


class DetectorOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    detector: str
    layer: Layer
    ts: float
    channel: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    fired: bool
    evidence: tuple[Evidence, ...] = ()
    explanation: str


class Detector(ABC):
    """Base class. Subclasses set ``name``, ``layer``, ``consumes`` and implement :meth:`update`."""

    name: ClassVar[str]
    layer: ClassVar[Layer]
    consumes: ClassVar[frozenset[EventKind]]
    fire_at: float = 0.5  # score threshold for ``fired``
    inhibit_until: float = 0.0  # mission seconds: no output before this (start-up transients)
    emit_floor: float = 0.3  # scores below this are not emitted at all

    def learn(self, event: Event) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Observe nominal data during calibration. Default: nothing to learn."""

    def freeze(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """End calibration: finalize baselines. Called once, before the first :meth:`update`."""

    @abstractmethod
    def update(self, event: Event) -> list[DetectorOutput]:
        """Process one event; return outputs for anything worth reporting."""

    def reset(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Drop run-time state but keep learned baselines (used when a session restarts)."""

    def flush(self) -> list[DetectorOutput]:
        """Emit anything still buffered at end of stream (e.g. an unfinished assembly step)."""
        return []

    def observe(self, output: DetectorOutput) -> list[DetectorOutput]:
        """Second-stage hook: see another detector's output (used by L5 correlation)."""
        return []

    def _out(
        self,
        ts: float,
        score: float,
        explanation: str,
        evidence: tuple[Evidence, ...],
        channel: str | None = None,
        sub: str | None = None,
    ) -> list[DetectorOutput]:
        """Build the standard output list; empty if the score is below the emit floor.

        ``sub`` appends a suffix to the detector name so one class can report several statistics.
        """
        score = min(1.0, max(0.0, float(score)))
        if score < self.emit_floor or ts < self.inhibit_until:
            return []
        return [
            DetectorOutput(
                detector=self.name if sub is None else f"{self.name}.{sub}",
                layer=self.layer,
                ts=ts,
                channel=channel,
                score=score,
                fired=score >= self.fire_at,
                evidence=evidence,
                explanation=explanation,
            )
        ]
