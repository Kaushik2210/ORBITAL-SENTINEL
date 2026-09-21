from __future__ import annotations

import numpy as np
import pytest

from sentinel_sim import bus, pcoe


@pytest.fixture(scope="module")
def trajectories() -> tuple[pcoe.BatteryTrajectory, pcoe.WheelTrajectory]:
    return pcoe.parametric_battery("B0005"), pcoe.parametric_wheel()


def run(
    tr: tuple[pcoe.BatteryTrajectory, pcoe.WheelTrajectory],
    aging: bus.AgingFn = bus.healthy_aging,
    n: int = 1500,
    seed: int = 1,
) -> tuple[np.ndarray, list[bus.BusSample]]:
    b = bus.PhysicsBus(seed, tr[0], tr[1], aging)
    samples = [b.step(k) for k in range(n)]
    return np.array([[s.values[c] for c in bus.BUS_CHANNELS] for s in samples]), samples


def col(x: np.ndarray, name: str) -> np.ndarray:
    return x[:, bus.BUS_CHANNELS.index(name)]


def test_is_deterministic_and_seed_sensitive(trajectories) -> None:  # type: ignore[no-untyped-def]
    a, _ = run(trajectories, n=200)
    b, _ = run(trajectories, n=200)
    c, _ = run(trajectories, n=200, seed=2)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_reset_restarts_the_same_stream(trajectories) -> None:  # type: ignore[no-untyped-def]
    pb = bus.PhysicsBus(3, *trajectories)
    first = [pb.step(k).values["batt_v_a"] for k in range(50)]
    pb.reset()
    assert first == [pb.step(k).values["batt_v_a"] for k in range(50)]


def test_channel_set_and_units_are_complete() -> None:
    assert set(bus.UNITS) == set(bus.BUS_CHANNELS)
    assert len(set(bus.BUS_CHANNELS)) == 14


def test_redundant_voltage_sensors_agree_up_to_calibration_offset(trajectories) -> None:  # type: ignore[no-untyped-def]
    x, _ = run(trajectories)
    diff = col(x, "batt_v_b") - col(x, "batt_v_a")
    assert diff.mean() == pytest.approx(0.05, abs=0.01)
    assert diff.std() < 0.05


def test_power_balance_holds_up_to_sensor_noise(trajectories) -> None:  # type: ignore[no-untyped-def]
    x, _ = run(trajectories)
    residual = col(x, "batt_i") - (col(x, "solar_i") - col(x, "load_i"))
    assert np.abs(residual).max() < 0.25  # ~5 sigma of the summed current-sensor noise
    assert abs(residual.mean()) < 0.01


def test_state_of_charge_stays_in_bounds_and_high(trajectories) -> None:  # type: ignore[no-untyped-def]
    _, samples = run(trajectories, n=3000)
    soc = np.array([s.truth["soc"] for s in samples])
    assert soc.min() > 0.5
    assert soc.max() <= 1.0


def test_no_nans_and_ranges_are_physical(trajectories) -> None:  # type: ignore[no-untyped-def]
    x, _ = run(trajectories)
    assert np.isfinite(x).all()
    assert col(x, "batt_v_a").min() > 24.0
    assert col(x, "batt_v_a").max() < 34.0
    assert col(x, "solar_i").min() > -0.3  # no meaningful negative solar current
    assert col(x, "rw1_vib_a").min() > 0


def test_aging_sweep_raises_failing_wheel_vibration_but_not_the_healthy_one(trajectories) -> None:  # type: ignore[no-untyped-def]
    def sweep(k: int) -> tuple[float, float]:
        p = 0.05 + 0.95 * min(1.0, k / 1500)
        return p, p

    x, _ = run(trajectories, aging=sweep)
    n = 150
    assert col(x, "rw1_vib_a")[-n:].mean() / col(x, "rw1_vib_a")[:n].mean() > 2.0
    assert (
        col(x, "rw1_vib_b")[-n:].mean() / col(x, "rw1_vib_b")[:n].mean() > 2.0
    )  # both sensors see it
    assert col(x, "rw2_vib")[-n:].mean() / col(x, "rw2_vib")[:n].mean() < 1.6


def test_truth_is_separate_from_sensor_values(trajectories) -> None:  # type: ignore[no-untyped-def]
    _, samples = run(trajectories, n=5)
    for s in samples:
        assert "soc" in s.truth
        assert "soc" not in s.values


def test_source_labels_propagate(trajectories) -> None:  # type: ignore[no-untyped-def]
    pb = bus.PhysicsBus(1, *trajectories)
    assert pb.synthetic_sources == {
        "battery": "synthetic-parametric",
        "wheel": "synthetic-parametric",
    }
