from __future__ import annotations

import math

import numpy as np
import pytest

from sentinel_core.detection.base import DetectorOutput
from sentinel_core.detection.engine import DetectionEngine
from sentinel_core.detection.physics import (
    PairSpec,
    PowerBalance,
    RedundantSensors,
    StepAssembler,
)
from sentinel_core.detection.statistical import StatisticalDetector, fit_channel
from sentinel_core.events import TelemetryEvent
from sentinel_core.ingest import Ingestor
from sentinel_sim import bus, pcoe
from sentinel_sim.mission import Injector, Mission, MissionConfig

STEP = 60.0


def tel(ch: str, k: int, v: float, ts_rx: float | None = None) -> TelemetryEvent:
    return TelemetryEvent(
        ts=k * STEP, channel=ch, value=v, ts_rx=k * STEP if ts_rx is None else ts_rx
    )


def signal(
    n: int, seed: int = 0, period: int = 90, noise: float = 0.05, offset: int = 0
) -> np.ndarray:
    """Sine with the given period; ``offset`` is the absolute step of the first sample."""
    rng = np.random.default_rng(seed)
    k = np.arange(offset, offset + n)
    return 10 + 2 * np.sin(2 * np.pi * k / period) + rng.normal(0, noise, n)


def calibrated_l1(period: int | None = 90, n: int = 900) -> StatisticalDetector:
    det = StatisticalDetector({"x": period})
    for k, v in enumerate(signal(n)):
        det.learn(tel("x", k, float(v)))
    det.freeze()
    return det


def run(det: StatisticalDetector, values: list[float], start: int = 1000) -> list[DetectorOutput]:
    return [o for i, v in enumerate(values) for o in det.update(tel("x", start + i, v))]


def fired_subs(outs: list[DetectorOutput]) -> set[str]:
    return {o.detector.rsplit(".", 1)[-1] for o in outs if o.fired}


# ------------------------------------------------------------------- L1


def test_nominal_seasonal_signal_is_quiet_with_a_seasonal_baseline() -> None:
    det = calibrated_l1()
    live = signal(600, seed=7, offset=1000)
    assert [o for o in run(det, live.tolist()) if o.fired] == []


def test_without_a_seasonal_baseline_the_same_signal_would_alarm() -> None:
    det = StatisticalDetector({"x": None})
    for k, v in enumerate(signal(900)):
        det.learn(tel("x", k, float(v)))
    det.freeze()
    # a plain range/z model on a 90-step oscillation is what the seasonal baseline exists to avoid
    assert det.models["x"].sigma > 1.0


def test_step_bias_is_detected_quickly_and_persistently() -> None:
    det = calibrated_l1()
    live = signal(300, seed=8, offset=1000)
    live[100:] += 3.0
    outs = run(det, live.tolist())
    first = min(o.ts for o in outs if o.fired) / STEP - 1000
    assert 100 <= first <= 102
    late = {o.detector for o in outs if o.fired and o.ts / STEP - 1000 > 250}
    assert late, "a persistent bias must stay flagged, not be absorbed by the slow reference"


def test_detrended_cusum_absorbs_a_ramp_much_slower_than_its_memory() -> None:
    """L1's CUSUM tracks slow trends (memory ~20 steps): a very slow in-limits ramp is not its job.

    Slow drift is what L3 redundancy and physics checks exist for. This pins the limitation.
    """
    det = calibrated_l1()
    live = signal(400, seed=9, offset=1000)
    live[50:] += np.linspace(0, 0.02, 350)  # 0.02 units (0.4 sigma) over 350 steps
    subs = fired_subs(run(det, live.tolist()))
    assert "cusum" not in subs
    assert "range" not in subs


def test_dropout_flatline_and_noise_changes() -> None:
    det = calibrated_l1()
    base = signal(200, seed=10, offset=1000).tolist()
    nan = base.copy()
    nan[50] = float("nan")
    assert "dropout" in fired_subs(run(det, nan))

    det = calibrated_l1()
    stuck = base.copy()
    stuck[60:] = [stuck[60]] * 140
    assert "flatline" in fired_subs(run(det, stuck))

    det = calibrated_l1()
    noisy = signal(200, seed=11, noise=0.5, offset=1000).tolist()  # 10x the calibrated noise
    assert "variance" in fired_subs(run(det, noisy))


def test_out_of_range_value_is_flagged_with_evidence() -> None:
    det = calibrated_l1()
    outs = run(det, [10.0, 10.0, 50.0])
    (r,) = [o for o in outs if o.detector.endswith(".range")]
    assert r.fired
    assert r.evidence[0].observed == 50.0


def test_constant_channels_do_not_trigger_flatline() -> None:
    det = StatisticalDetector()
    for k in range(200):
        det.learn(tel("c", k, 1.0))
    det.freeze()
    assert det.models["c"].constant
    assert [o for k in range(200, 300) for o in det.update(tel("c", k, 1.0)) if o.fired] == []


def test_replayed_sample_uses_receive_time_for_the_seasonal_phase() -> None:
    det = calibrated_l1()
    # a sample stamped with a stale packet time but received now must be judged at the receive step
    e = TelemetryEvent(
        ts=5 * STEP, channel="x", value=float(signal(1, offset=1000)[0]), ts_rx=1000 * STEP
    )
    assert [o for o in det.update(e) if o.fired] == []


def test_inhibit_suppresses_alarms_but_not_learning() -> None:
    det = calibrated_l1()
    det.inhibit_until = 2000 * STEP
    assert run(det, [50.0, 50.0]) == []


def test_calibration_needs_enough_finite_samples() -> None:
    with pytest.raises(ValueError, match="calibration samples"):
        fit_channel("x", list(range(10)), [1.0] * 10, None)
    with pytest.raises(ValueError, match="calibration samples"):
        fit_channel("x", list(range(100)), [float("nan")] * 100, None)


# ------------------------------------------------------------------- L3 primitives


def test_step_assembler_emits_complete_steps_and_drops_incomplete_ones() -> None:
    asm = StepAssembler(frozenset({"a", "b"}))
    assert asm.add(tel("a", 0, 1.0)) is None
    assert asm.add(tel("b", 0, 2.0)) is None
    assert asm.add(tel("a", 1, 3.0)) == (0, {"a": 1.0, "b": 2.0})  # step 0 completes when 1 begins
    assert asm.add(tel("a", 2, 5.0)) is None  # step 1 never got "b": dropped, not emitted
    assert asm.add(tel("zzz", 2, 9.0)) is None  # irrelevant channels are ignored
    asm.reset()
    assert asm.flush() is None


def test_pair_spec_combines() -> None:
    assert PairSpec("a", "b", "diff", "x").combine(1.0, 3.0) == 2.0
    assert PairSpec("a", "b", "ratio", "x").combine(2.0, 3.0) == 1.5
    assert math.isnan(PairSpec("a", "b", "ratio", "x").combine(0.0, 3.0))


def pair_detector(n: int = 600) -> RedundantSensors:
    rng = np.random.default_rng(1)
    det = RedundantSensors((PairSpec("a", "b", "diff", "test pair"),))
    for k in range(n):
        truth = 5 + math.sin(k / 9)
        det.learn(tel("a", k, truth + rng.normal(0, 0.02)))
        det.learn(tel("b", k, truth + 0.05 + rng.normal(0, 0.02)))
    det.freeze()
    return det


def feed_pair(det: RedundantSensors, n: int, fault: str, start: int = 100) -> list[DetectorOutput]:
    rng = np.random.default_rng(2)
    outs: list[DetectorOutput] = []
    for k in range(n):
        truth = 5 + math.sin(k / 9)
        a = truth + rng.normal(0, 0.02)
        b = truth + 0.05 + rng.normal(0, 0.02)
        if k >= start:
            if fault == "a_bias":
                a += 0.4
            elif fault == "b_bias":
                b += 0.4
            elif fault == "common":
                a += 0.4
                b += 0.4
        for o in det.update(tel("a", 1000 + k, a)) + det.update(tel("b", 1000 + k, b)):
            outs.append(o)
    return outs + det.flush()


def test_redundant_pair_is_quiet_when_healthy_and_when_both_move_together() -> None:
    assert [o for o in feed_pair(pair_detector(), 300, "none") if o.fired] == []
    assert [o for o in feed_pair(pair_detector(), 300, "common") if o.fired] == []


def test_divergence_fires_and_says_which_sensor_departed() -> None:
    a_side = [o for o in feed_pair(pair_detector(), 300, "a_bias") if o.fired]
    b_side = [o for o in feed_pair(pair_detector(), 300, "b_bias") if o.fired]
    assert a_side
    assert b_side

    def side(o: DetectorOutput) -> float:
        return float(next(e.observed for e in o.evidence if e.name == "sidedness"))  # type: ignore[arg-type]

    assert np.mean([side(o) for o in a_side[:5]]) > 0.2
    assert np.mean([side(o) for o in b_side[:5]]) < -0.2


def test_power_balance_flags_a_lying_current_sensor() -> None:
    rng = np.random.default_rng(3)
    pb = PowerBalance()
    for k in range(400):
        solar, load = 6 + math.sin(k / 9), 3.0
        for ch, v in (
            ("solar_i", solar),
            ("load_i", load),
            ("batt_i", solar - load + rng.normal(0, 0.03)),
        ):
            pb.learn(tel(ch, k, v))
    pb.freeze()
    outs = []
    for k in range(200):
        solar, load = 6 + math.sin(k / 9), 3.0
        bias = 0.8 if k >= 100 else 0.0
        for ch, v in (
            ("solar_i", solar),
            ("load_i", load),
            ("batt_i", solar - load + bias + rng.normal(0, 0.03)),
        ):
            outs += pb.update(tel(ch, 2000 + k, v))
    outs += pb.flush()
    fired = [o for o in outs if o.fired]
    assert fired
    assert min(o.ts for o in fired) / STEP == pytest.approx(2100, abs=2)
    assert not [o for o in outs if o.ts / STEP < 2098 and o.fired]


def test_calibration_of_l3_requires_data() -> None:
    with pytest.raises(ValueError, match="calibration samples"):
        RedundantSensors().freeze()


# ------------------------------------------------------------------- integration on the mission


class Fault(Injector):
    def __init__(self, kind: str, start: int) -> None:
        self.kind, self.start = kind, start

    def on_sensors(
        self, k: int, values: dict[str, float], truth: dict[str, float]
    ) -> dict[str, float]:
        if k < self.start:
            return values
        v = dict(values)
        if self.kind == "bias":
            v["batt_v_a"] += 0.6
        elif self.kind == "common":
            v["batt_v_a"] += 0.004 * (k - self.start)
            v["batt_v_b"] += 0.004 * (k - self.start)
        return v


def mission_events(seed: int, n: int, inj: tuple[Injector, ...] = ()) -> list[TelemetryEvent]:
    m = Mission(
        MissionConfig(seed=seed),
        injectors=inj,
        battery=pcoe.parametric_battery(),
        wheel=pcoe.parametric_wheel(),
    )
    ing = Ingestor(m.apids, m.config.frame_key, m.config.start)
    return [
        e for k in range(n) for e in ing.process_tick(m.tick(k)) if isinstance(e, TelemetryEvent)
    ]


PERIODS: dict[str, int | None] = {c: 90 for c in bus.BUS_CHANNELS if "speed" not in c}


@pytest.fixture(scope="module")
def engine() -> DetectionEngine:
    eng = DetectionEngine(
        [StatisticalDetector(PERIODS), RedundantSensors(), PowerBalance()], inhibit_s=150 * STEP
    )
    eng.calibrate(mission_events(101, 1200))
    return eng


def replay(
    engine: DetectionEngine, inj: tuple[Injector, ...], n: int = 700
) -> list[DetectorOutput]:
    engine.reset()
    outs = [o for e in mission_events(202, n, inj) for o in engine.process(e)]
    return outs + engine.flush()


def test_nominal_mission_has_no_fired_alerts_before_the_fault_window(
    engine: DetectionEngine,
) -> None:
    outs = replay(engine, ())
    assert [o for o in outs if o.fired] == []


def test_sensor_bias_is_caught_by_single_channel_and_by_the_redundancy_check(
    engine: DetectionEngine,
) -> None:
    outs = replay(engine, (Fault("bias", 400),))
    fired = [o for o in outs if o.fired and o.ts / STEP >= 400]
    layers = {o.layer.value for o in fired}
    assert {"L1", "L3"} <= layers
    assert min(o.ts for o in fired) / STEP <= 402
    assert not [o for o in outs if o.fired and o.ts / STEP < 400]


def test_common_mode_drift_is_invisible_to_the_redundancy_check(engine: DetectionEngine) -> None:
    """Both sensors moving together looks like the real quantity changing: L3 stays quiet."""
    outs = replay(engine, (Fault("common", 300),))
    assert [o for o in outs if o.fired and o.layer.value == "L3"] == []
