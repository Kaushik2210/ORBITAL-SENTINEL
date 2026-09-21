"""Run scenarios through ingestion, the detection engine and incident grouping."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from sentinel_core.detection.base import DetectorOutput
from sentinel_core.detection.engine import DetectionEngine, IncidentBuilder, IncidentWindow
from sentinel_core.detection.factory import standard_engine
from sentinel_core.ingest import Ingestor
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import bus, pcoe, smap_msl
from sentinel_sim.knowledge import mission_knowledge
from sentinel_sim.mission import DEFAULT_FRAME_KEY, Mission, MissionConfig
from sentinel_sim.scenarios.build import BuiltScenario
from sentinel_sim.scenarios.spec import QUIET_START

CALIBRATION_SEED = 900_001  # never used by any scenario
CALIBRATION_STEPS = 1800
BUS_PERIODS: dict[str, int | None] = {
    c: (None if "speed" in c else 90) for c in bus.BUS_CHANNELS
}  # wheel speeds are near-constant: no orbital season


def group_of(channel: str) -> str:
    if channel in bus.EPS_CHANNELS:
        return "EPS"
    if channel in bus.ADCS_CHANNELS:
        return "ADCS"
    return smap_msl.group_of(channel)


@dataclass(slots=True)
class RunResult:
    scenario_id: str
    outputs: list[DetectorOutput]
    windows: list[IncidentWindow]
    steps: int
    wall_s: float
    frames: int = 0
    weather_events: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def calibrated_engine(
    data_root: Path = Path("data/raw"),
    battery: pcoe.BatteryTrajectory | None = None,
    wheel: pcoe.WheelTrajectory | None = None,
    steps: int = CALIBRATION_STEPS,
) -> DetectionEngine:
    """Commission the standard engine on a nominal run (a seed no scenario uses)."""
    mission = Mission(
        MissionConfig(seed=CALIBRATION_SEED, start=QUIET_START, data_root=data_root),
        battery=battery,
        wheel=wheel,
    )
    ing = Ingestor(mission.apids, mission.config.frame_key, mission.config.start)
    events = [e for k in range(steps) for e in ing.process_tick(mission.tick(k))]
    engine = standard_engine(mission_knowledge(), BUS_PERIODS, group_of)
    engine.calibrate(events)
    return engine


def run_scenario(
    built: BuiltScenario, engine: DetectionEngine, quiet_s: float = 900.0
) -> RunResult:
    t0 = time.perf_counter()
    engine.reset()
    mission = built.mission
    mission.reset()
    ing = Ingestor(mission.apids, DEFAULT_FRAME_KEY, mission.config.start)
    builder = IncidentBuilder(quiet_s=quiet_s)
    outputs: list[DetectorOutput] = []
    windows: list[IncidentWindow] = []
    frames = 0

    def take(outs: list[DetectorOutput]) -> None:
        for o in outs:
            outputs.append(o)
            builder.add(o)

    for w in built.weather:
        take(engine.process(w))
    for k in range(built.spec.steps):
        tick = mission.tick(k)
        frames += len(tick.frames)
        for e in ing.process_tick(tick):
            take(engine.process(e))
        windows += builder.advance(tick.ts)
    take(engine.flush())
    windows += builder.flush()
    return RunResult(
        built.spec.id, outputs, windows, built.spec.steps, time.perf_counter() - t0, frames,
        len(built.weather),
    )  # fmt: skip


def step_of_ts(ts: float) -> int:
    return round(ts / STEP_SECONDS)
