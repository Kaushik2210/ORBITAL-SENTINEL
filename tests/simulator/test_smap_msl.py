from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from sentinel_sim import smap_msl as sm

REAL_ROOT = Path("data/raw")
needs_real = pytest.mark.skipif(
    not (REAL_ROOT / "smap_msl" / "labeled_anomalies.csv").is_file(),
    reason="real SMAP/MSL data not downloaded (run `make data`)",
)

LABELS = (
    "chan_id,spacecraft,anomaly_sequences,class,num_values\n"
    'P-1,SMAP,"[[10, 20], [50, 60]]","[point, contextual]",100\n'
    'P-2,SMAP,"[[30, 40]]",[point],100\n'
    'P-2,SMAP,"[[35, 45]]",[point],100\n'
    'M-1,MSL,"[[5, 9]]",[contextual],100\n'
)


def write_dataset(root: Path, chans: dict[str, int]) -> None:
    (root / "smap_msl" / "train").mkdir(parents=True)
    (root / "smap_msl" / "test").mkdir(parents=True)
    (root / "smap_msl" / "labeled_anomalies.csv").write_text(LABELS, encoding="utf-8")
    rng = np.random.default_rng(0)
    for ch, n_feat in chans.items():
        for split, n in (("train", 30), ("test", 100)):
            arr = np.zeros((n, n_feat))
            arr[:, 0] = rng.normal(size=n)
            arr[:, 1:] = rng.integers(0, 2, size=(n, n_feat - 1))
            np.save(root / "smap_msl" / split / f"{ch}.npy", arr)


def test_merge_intervals_merges_overlap_and_adjacency() -> None:
    assert sm.merge_intervals([(5, 9), (1, 3), (4, 4)]) == ((1, 9),)
    assert sm.merge_intervals([(1, 3), (5, 6)]) == ((1, 3), (5, 6))
    assert sm.merge_intervals([]) == ()


def test_labels_union_duplicate_channel_rows(tmp_path: Path) -> None:
    write_dataset(tmp_path, {"P-1": 5, "P-2": 5, "M-1": 5})
    labels = sm.load_labels(tmp_path)
    assert labels["P-2"].intervals == ((30, 45),)
    assert labels["P-2"].n_label_rows == 2
    assert labels["P-1"].intervals == ((10, 20), (50, 60))
    assert labels["P-1"].classes == ("point", "contextual")
    assert labels["M-1"].spacecraft == "MSL"


def test_mask_marks_inclusive_intervals() -> None:
    lab = sm.ChannelLabels("X", "SMAP", ((2, 3), (8, 20)), ("point",), 10, 1)
    m = lab.mask(10)
    assert m.tolist() == [False, False, True, True, False, False, False, False, True, True]


def test_unlabeled_channel_has_no_spacecraft(tmp_path: Path) -> None:
    write_dataset(tmp_path, {"P-1": 5, "T-10": 5})
    labels = sm.load_labels(tmp_path)
    assert "T-10" not in labels
    series = sm.load_channel(tmp_path, "T-10", labels)
    assert series.spacecraft is None
    assert sm.load_channel(tmp_path, "P-1", labels).spacecraft == "SMAP"


def test_missing_data_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(sm.DataUnavailableError):
        sm.load_labels(tmp_path)
    with pytest.raises(sm.DataUnavailableError):
        sm.available_channels(tmp_path)
    write_dataset(tmp_path, {"P-1": 5})
    with pytest.raises(sm.DataUnavailableError):
        sm.load_channel(tmp_path, "nope")


def test_load_channel_splits_telemetry_and_commands(tmp_path: Path) -> None:
    write_dataset(tmp_path, {"P-1": 5})
    s = sm.load_channel(tmp_path, "P-1")
    assert s.train.shape == (30,)
    assert s.test.shape == (100,)
    assert s.n_cmd == 4
    raw = np.load(tmp_path / "smap_msl" / "test" / "P-1.npy")
    assert np.array_equal(sm.unpack_commands(s.test_cmd, s.n_cmd), raw[:, 1:])


@given(
    hnp.arrays(
        np.float64,
        hnp.array_shapes(min_dims=2, max_dims=2, min_side=1, max_side=64),
        elements=st.sampled_from([0.0, 1.0]),
    )
)
def test_command_pack_unpack_round_trip(cmd: np.ndarray) -> None:
    if cmd.shape[1] > 64:
        return
    assert np.array_equal(sm.unpack_commands(sm.pack_commands(cmd), cmd.shape[1]), cmd)


def test_multi_hot_is_preserved_not_collapsed_to_argmax() -> None:
    cmd = np.array([[1.0, 0.0, 1.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    assert sm.pack_commands(cmd).tolist() == [0b101, 0, 0b010]


def test_54_command_columns_fit() -> None:
    cmd = np.ones((2, 54))
    assert int(sm.pack_commands(cmd)[0]) == (1 << 54) - 1


def test_more_than_64_columns_rejected() -> None:
    with pytest.raises(ValueError, match="64 bits"):
        sm.pack_commands(np.zeros((1, 65)))


def test_group_of_is_prefix_letter() -> None:
    assert sm.group_of("D-12") == "D"


@needs_real
def test_real_labels_match_documented_counts() -> None:
    labels = sm.load_labels(REAL_ROOT)
    assert len(labels) == 81  # DATASETS.md: 81 unique labeled channels
    assert "T-10" not in labels
    assert labels["P-2"].n_label_rows == 2
    assert sum(len(v.classes) for v in labels.values()) == 105
    assert {v.spacecraft for v in labels.values()} == {"SMAP", "MSL"}


@needs_real
def test_real_arrays_are_all_present_and_lengths_match_labels() -> None:
    labels = sm.load_labels(REAL_ROOT)
    assert len(sm.available_channels(REAL_ROOT)) == 82
    for ch, lab in labels.items():
        assert sm.load_channel(REAL_ROOT, ch, labels).test.shape[0] == lab.num_values
