"""Run many scenarios in parallel, one calibrated engine per worker process."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from sentinel_core.detection.engine import DetectionEngine
from sentinel_sim import pcoe
from sentinel_sim.scenarios.build import build_scenario
from sentinel_sim.scenarios.spec import GroundTruth, ScenarioSpec

from .pipeline import RunResult, calibrated_engine, run_scenario

_ENGINE: DetectionEngine | None = None
_DATA_ROOT = Path("data/raw")
_BATT: pcoe.BatteryTrajectory | None = None
_WHEEL: pcoe.WheelTrajectory | None = None


@dataclass(slots=True)
class SuiteItem:
    spec: ScenarioSpec
    truth: GroundTruth
    result: RunResult


def _init(data_root: str) -> None:
    global _ENGINE, _DATA_ROOT, _BATT, _WHEEL
    _DATA_ROOT = Path(data_root)
    _BATT, _WHEEL = pcoe.load_battery(_DATA_ROOT), pcoe.load_wheel(_DATA_ROOT)
    _ENGINE = calibrated_engine(_DATA_ROOT, _BATT, _WHEEL)


def _work(spec: ScenarioSpec) -> SuiteItem:
    assert _ENGINE is not None
    built = build_scenario(spec, _DATA_ROOT, battery=_BATT, wheel=_WHEEL)
    return SuiteItem(spec, built.truth, run_scenario(built, _ENGINE))


def run_suite(
    specs: list[ScenarioSpec], data_root: Path = Path("data/raw"), workers: int = 8
) -> list[SuiteItem]:
    if workers <= 1:
        _init(str(data_root))
        return [_work(s) for s in specs]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(data_root),)) as pool:
        return list(pool.map(_work, specs))
