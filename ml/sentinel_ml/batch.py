"""Run many scenarios in parallel, one calibrated engine per worker process."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from sentinel_core.detection.engine import DetectionEngine
from sentinel_sim import pcoe
from sentinel_sim.scenarios.build import build_scenario
from sentinel_sim.scenarios.spec import ScenarioSpec, instantiate

from .dataset import RunRecord, make_record
from .pipeline import calibrated_engine, run_scenario

_ENGINE: DetectionEngine | None = None
_DATA_ROOT = Path("data/raw")
_BATT: pcoe.BatteryTrajectory | None = None
_WHEEL: pcoe.WheelTrajectory | None = None


def _init(data_root: str) -> None:
    global _ENGINE, _DATA_ROOT, _BATT, _WHEEL
    _DATA_ROOT = Path(data_root)
    _BATT, _WHEEL = pcoe.load_battery(_DATA_ROOT), pcoe.load_wheel(_DATA_ROOT)
    _ENGINE = calibrated_engine(_DATA_ROOT, _BATT, _WHEEL)


def _work(job: tuple[ScenarioSpec, int, bool]) -> RunRecord:
    base, variant, keep = job
    assert _ENGINE is not None
    spec = instantiate(base, variant)
    built = build_scenario(spec, _DATA_ROOT, battery=_BATT, wheel=_WHEEL)
    return make_record(spec, variant, built.truth, run_scenario(built, _ENGINE), keep_windows=keep)


def run_suite(
    specs: list[ScenarioSpec],
    variants: list[int] | int = 1,
    data_root: Path = Path("data/raw"),
    workers: int = 8,
    keep_windows: bool = False,
) -> list[RunRecord]:
    """Run every scenario for each variant index; returns one record per (scenario, variant)."""
    vs = list(range(variants)) if isinstance(variants, int) else variants
    jobs = [(s, v, keep_windows and v == 0) for s in specs for v in vs]
    if workers <= 1:
        _init(str(data_root))
        return [_work(j) for j in jobs]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(data_root),)) as pool:
        return list(pool.map(_work, jobs, chunksize=4))
