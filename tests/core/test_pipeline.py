from __future__ import annotations

from pathlib import Path

import pytest

from sentinel_core.detection.engine import DetectionEngine
from sentinel_sim import pcoe
from sentinel_sim.pipeline import calibrated_engine, run_scenario
from sentinel_sim.scenarios.build import build_scenario
from sentinel_sim.scenarios.spec import ScenarioSpec, load_all

SPECS: dict[str, ScenarioSpec] = {s.id: s for s in load_all(Path("data/scenarios"))}
BATT, WHEEL = pcoe.parametric_battery(), pcoe.parametric_wheel()


@pytest.fixture(scope="module")
def engine() -> DetectionEngine:
    return calibrated_engine(Path("nonexistent"), BATT, WHEEL)


def run(engine: DetectionEngine, sid: str):  # type: ignore[no-untyped-def]
    built = build_scenario(SPECS[sid], Path("nonexistent"), battery=BATT, wheel=WHEEL)
    return built, run_scenario(built, engine)


@pytest.mark.slow
def test_nominal_scenario_produces_no_incident(engine: DetectionEngine) -> None:
    _, res = run(engine, "nominal_b")
    assert res.windows == []
    assert res.frames == 1800 * 2


@pytest.mark.slow
def test_attack_scenario_is_detected_promptly_and_grouped_into_an_incident(
    engine: DetectionEngine,
) -> None:
    built, res = run(engine, "auth_bruteforce")
    truth_start = built.truth.start_step
    assert truth_start is not None
    assert res.windows
    first = min(o.ts for o in res.outputs if o.fired) / 60
    assert 0 <= first - truth_start <= 5
    assert any(o.detector == "l4.auth_anomaly" for w in res.windows for o in w.fired)


@pytest.mark.slow
def test_runs_are_reproducible_and_do_not_leak_state_between_scenarios(
    engine: DetectionEngine,
) -> None:
    _, first = run(engine, "sensor_stuck_battv")
    run(engine, "cmd_unauthorized")  # a different scenario in between
    _, second = run(engine, "sensor_stuck_battv")
    assert [(o.detector, o.ts, o.score) for o in first.outputs] == [
        (o.detector, o.ts, o.score) for o in second.outputs
    ]
