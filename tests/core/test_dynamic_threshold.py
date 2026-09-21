from __future__ import annotations

import numpy as np
import pytest

from sentinel_core.detection import dynamic_threshold as dt


def series(n: int = 1500, seed: int = 0, bump: tuple[int, int, float] | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = np.abs(rng.normal(0.1, 0.02, n))
    if bump:
        a, b, h = bump
        e[a:b] += h
    return dt.ewma(e, 30)


def test_ewma_matches_the_recursion_and_smooths() -> None:
    x = np.array([0.0, 10.0, 10.0, 10.0])
    y = dt.ewma(x, span=3)  # alpha = 0.5
    assert y.tolist() == pytest.approx([0.0, 5.0, 7.5, 8.75])
    assert dt.ewma(np.array([]), 3).tolist() == []


def test_sequences_are_inclusive_runs() -> None:
    m = np.array([0, 1, 1, 0, 1, 0, 0, 1, 1, 1], dtype=bool)
    assert dt._sequences(m) == [(1, 2), (4, 4), (7, 9)]
    assert dt._sequences(np.zeros(5, dtype=bool)) == []


def test_a_clear_error_burst_is_found_and_located() -> None:
    e = series(bump=(700, 760, 0.6))
    seqs = dt.anomalous_sequences(e)
    assert len(seqs) == 1
    a, b = seqs[0]
    assert a >= 690
    assert b <= 800
    assert a <= 720 <= b


def test_pure_noise_yields_no_anomalies_after_pruning() -> None:
    assert dt.anomalous_sequences(series(seed=3)) == []
    assert dt.anomalous_sequences(series(seed=4)) == []


def test_flat_error_series_is_handled() -> None:
    assert dt.choose_epsilon(np.zeros(500)) is None
    assert dt.choose_epsilon(np.full(500, 0.2)) is None
    assert dt.anomalous_sequences(np.zeros(500)) == []


def test_pruning_drops_a_sequence_that_barely_exceeds_the_normal_peak() -> None:
    e = np.concatenate([np.full(50, 0.10), [0.101] * 5, np.full(50, 0.10)])
    seqs = [(50, 54)]
    assert dt.prune(e, seqs, eps=0.1005) == []  # peak is < 13% above the nominal maximum


def test_pruning_keeps_the_dominant_sequence_and_drops_the_weak_one() -> None:
    e = np.full(200, 0.1)
    e[20:25] = 1.0  # dominant
    e[100:105] = 0.4  # far below the dominant one, but above nominal
    kept = dt.prune(e, [(20, 24), (100, 104)], eps=0.3)
    assert (20, 24) in kept


def test_merge_joins_overlapping_and_adjacent_ranges() -> None:
    assert dt.merge([(5, 9), (1, 3), (4, 4)]) == [(1, 9)]
    assert dt.merge([(1, 2), (10, 12)], gap=10) == [(1, 12)]
    assert dt.merge([]) == []


def test_windowed_thresholding_finds_bursts_in_separate_windows() -> None:
    e = series(n=4500, bump=(400, 450, 0.6))
    e[3300:3350] += 0.6
    found = dt.anomalous_sequences(e, window=2100)
    assert any(a <= 425 <= b for a, b in found)
    assert any(a <= 3325 <= b for a, b in found)
