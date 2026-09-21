"""Assemble the standard detector suite."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .base import Detector
from .engine import DetectionEngine
from .environment import SimultaneousUpset, SpaceWeatherOverlap
from .physics import PowerBalance, RedundantSensors
from .protocol import (
    AuthAnomaly,
    AuthTagIntegrity,
    CommandPolicy,
    LinkShift,
    MissionKnowledge,
    RateAnomaly,
    SequenceIntegrity,
    TimestampFreshness,
)
from .statistical import StatisticalDetector

DEFAULT_INHIBIT_S = 150 * 60.0  # start-up transient (ADR 0010)


def standard_detectors(
    knowledge: MissionKnowledge,
    periods: Mapping[str, int | None],
    group_of: Callable[[str], str],
    with_physics: bool = True,
) -> list[Detector]:
    dets: list[Detector] = [
        SequenceIntegrity(),
        TimestampFreshness(),
        AuthTagIntegrity(),
        CommandPolicy(knowledge),
        AuthAnomaly(knowledge),
        LinkShift(),
        RateAnomaly(),
        StatisticalDetector(periods),
        SpaceWeatherOverlap(),
        SimultaneousUpset(group_of),
    ]
    if with_physics:
        dets += [RedundantSensors(), PowerBalance()]
    return dets


def standard_engine(
    knowledge: MissionKnowledge,
    periods: Mapping[str, int | None],
    group_of: Callable[[str], str],
    inhibit_s: float = DEFAULT_INHIBIT_S,
    with_physics: bool = True,
) -> DetectionEngine:
    return DetectionEngine(
        standard_detectors(knowledge, periods, group_of, with_physics), inhibit_s=inhibit_s
    )
