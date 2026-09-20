from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sentinel_sim import pcoe

REAL_ROOT = Path("data/raw")
needs_battery = pytest.mark.skipif(
    not (REAL_ROOT / pcoe.BATTERY_ZIP).is_file(), reason="PCoE battery not downloaded"
)
needs_bearings = pytest.mark.skipif(
    not (Path("data/cache/ims_test2_features.npz")).is_file(), reason="IMS features not extracted"
)


def test_parametric_battery_is_deterministic_and_labeled() -> None:
    a, b = pcoe.parametric_battery("B0005"), pcoe.parametric_battery("B0005")
    assert a.source == "synthetic-parametric"
    assert np.array_equal(a.capacity_frac, b.capacity_frac)
    assert not np.array_equal(a.capacity_frac, pcoe.parametric_battery("B0006").capacity_frac)


def test_parametric_battery_fades_and_resistance_grows() -> None:
    t = pcoe.parametric_battery()
    assert t.capacity_at(0.0) > t.capacity_at(1.0)
    assert 0.65 < t.capacity_at(1.0) < 0.80
    assert t.resistance_at(1.0) > t.resistance_at(0.0)


def test_parametric_wheel_failing_bearing_separates() -> None:
    w = pcoe.parametric_wheel()
    assert w.source == "synthetic-parametric"
    assert w.rms.shape == (pcoe.IMS_TEST2_SNAPSHOTS, 4)
    n = w.rms.shape[0] // 10
    ratios = w.rms[-n:].mean(axis=0) / w.rms[:n].mean(axis=0)
    assert ratios[0] > 2.0 > ratios[1:].max()


def test_missing_archives_fall_back_to_labeled_synthetic(tmp_path: Path) -> None:
    assert pcoe.load_battery(tmp_path).source == "synthetic-parametric"
    assert pcoe.load_wheel(tmp_path, cache_dir=tmp_path / "cache").source == "synthetic-parametric"


def test_wheel_cache_is_used_when_present(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    rms = np.ones((5, 4))
    np.savez_compressed(cache / "ims_test2_features.npz", rms=rms, kurtosis=rms * 3)
    w = pcoe.load_wheel(tmp_path, cache_dir=cache)
    assert w.source == "pcoe"
    assert np.array_equal(w.rms, rms)


@needs_battery
def test_real_b0005_matches_documented_fade() -> None:
    b = pcoe.load_battery(REAL_ROOT, "B0005")
    assert b.source == "pcoe"
    assert b.capacity0_ah == pytest.approx(1.856, abs=0.001)  # DATASETS.md
    assert b.capacity_frac[-1] == pytest.approx(1.0 - 0.286, abs=0.005)
    assert len(b.capacity_frac) == 168  # discharge cycles
    assert b.resistance_at(1.0) > b.resistance_at(0.0)


@needs_battery
@pytest.mark.parametrize("cell", pcoe.CANONICAL_CELLS)
def test_canonical_cells_lose_capacity(cell: str) -> None:
    b = pcoe.load_battery(REAL_ROOT, cell)
    assert 0.5 < b.capacity_at(1.0) < 0.85


@needs_bearings
def test_real_wheel_failing_bearing_rms_ratio() -> None:
    w = pcoe.load_wheel(REAL_ROOT)
    assert w.source == "pcoe"
    assert w.rms.shape == (984, 4)
    n = 98
    ratio = w.rms[-n:, 0].mean() / w.rms[:n, 0].mean()
    assert ratio == pytest.approx(2.80, abs=0.05)  # DATASETS.md
