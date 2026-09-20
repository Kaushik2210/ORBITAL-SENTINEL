"""Loaders for the real SMAP/MSL arrays and labels, encoding the Phase 1 findings.

* Column 0 is telemetry; columns 1.. are **multi-hot** command indicators (not one-hot), so they are
  packed into an integer bitmask per timestep.
* ``T-10`` has arrays but no label row: it is *unlabeled* and must be excluded from scoring.
* ``P-2`` has two label rows: its labels are the merged union of both rows' intervals.
* Intervals are inclusive ``[start, end]`` indices into the **test** array.
"""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

SUBDIR = "smap_msl"
Interval = tuple[int, int]


class DataUnavailableError(FileNotFoundError):
    """The real dataset is not on disk; callers fall back to the labeled synthetic generator."""


@dataclass(frozen=True, slots=True)
class ChannelLabels:
    channel: str
    spacecraft: str
    intervals: tuple[Interval, ...]  # merged, sorted, inclusive
    classes: tuple[str, ...]  # 'point' | 'contextual', one per original sequence
    num_values: int
    n_label_rows: int

    def mask(self, length: int) -> NDArray[np.bool_]:
        m = np.zeros(length, dtype=np.bool_)
        for a, b in self.intervals:
            m[a : min(b, length - 1) + 1] = True
        return m


@dataclass(frozen=True, slots=True)
class ChannelSeries:
    channel: str
    spacecraft: str | None  # None for the unlabeled channel
    train: NDArray[np.float64]
    test: NDArray[np.float64]
    train_cmd: NDArray[np.uint64]
    test_cmd: NDArray[np.uint64]
    n_cmd: int


def merge_intervals(intervals: list[Interval]) -> tuple[Interval, ...]:
    """Merge overlapping or touching inclusive intervals."""
    out: list[Interval] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def load_labels(root: Path) -> dict[str, ChannelLabels]:
    path = root / SUBDIR / "labeled_anomalies.csv"
    if not path.is_file():
        raise DataUnavailableError(str(path))
    rows: dict[str, list[dict[str, str]]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.setdefault(row["chan_id"], []).append(row)
    out: dict[str, ChannelLabels] = {}
    for chan, rs in rows.items():
        intervals: list[Interval] = []
        classes: list[str] = []
        for r in rs:
            intervals += [(int(a), int(b)) for a, b in ast.literal_eval(r["anomaly_sequences"])]
            classes += [c.strip() for c in r["class"].strip("[]").split(",")]
        out[chan] = ChannelLabels(
            channel=chan,
            spacecraft=rs[0]["spacecraft"],
            intervals=merge_intervals(intervals),
            classes=tuple(classes),
            num_values=int(rs[0]["num_values"]),
            n_label_rows=len(rs),
        )
    return out


def pack_commands(cmd: NDArray[np.float64]) -> NDArray[np.uint64]:
    """Multi-hot ``(n, k)`` 0/1 matrix -> ``(n,)`` bitmask (bit j = command j active). k <= 64."""
    k = cmd.shape[1]
    if k > 64:
        raise ValueError(f"cannot pack {k} command columns into 64 bits")
    if k == 0:
        return np.zeros(cmd.shape[0], dtype=np.uint64)
    bits = (cmd > 0.5).astype(np.uint64)
    return (bits << np.arange(k, dtype=np.uint64)).sum(axis=1, dtype=np.uint64)


def unpack_commands(mask: NDArray[np.uint64], n_cmd: int) -> NDArray[np.float64]:
    """Inverse of :func:`pack_commands`, for models that want the dense multi-hot matrix."""
    shifts = np.arange(n_cmd, dtype=np.uint64)
    return ((mask[:, None] >> shifts) & np.uint64(1)).astype(np.float64)


def available_channels(root: Path) -> list[str]:
    d = root / SUBDIR / "test"
    if not d.is_dir():
        raise DataUnavailableError(str(d))
    return sorted(p.stem for p in d.glob("*.npy"))


def load_channel(
    root: Path, channel: str, labels: dict[str, ChannelLabels] | None = None
) -> ChannelSeries:
    train_p = root / SUBDIR / "train" / f"{channel}.npy"
    test_p = root / SUBDIR / "test" / f"{channel}.npy"
    if not (train_p.is_file() and test_p.is_file()):
        raise DataUnavailableError(f"{channel}: arrays not found under {root / SUBDIR}")
    train, test = np.load(train_p), np.load(test_p)
    if train.ndim != 2 or test.ndim != 2 or train.shape[1] != test.shape[1]:
        raise ValueError(f"{channel}: unexpected shapes {train.shape} / {test.shape}")
    lab = labels.get(channel) if labels else None
    return ChannelSeries(
        channel=channel,
        spacecraft=lab.spacecraft if lab else None,
        train=np.ascontiguousarray(train[:, 0]),
        test=np.ascontiguousarray(test[:, 0]),
        train_cmd=pack_commands(train[:, 1:]),
        test_cmd=pack_commands(test[:, 1:]),
        n_cmd=train.shape[1] - 1,
    )


def group_of(channel: str) -> str:
    """Display grouping by ID-prefix letter. This is a SYNTHETIC grouping: IDs are anonymized."""
    return channel.split("-", 1)[0]
