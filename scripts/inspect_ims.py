"""Inspect an extracted IMS bearing test directory (e.g. ``2nd_test/``); print a JSON report.

Computes per-snapshot RMS and kurtosis per channel and checks whether the documented failing
bearing separates from the healthy ones. Statistics in docs/DATASETS.md come from this output.
Usage: uv run python scripts/inspect_ims.py --dir <path/to/2nd_test> [--failing-channel 0]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.stats import kurtosis


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument(
        "--failing-channel", type=int, default=0, help="0-based channel of failing bearing"
    )
    args = ap.parse_args()

    files = sorted(p for p in args.dir.iterdir() if p.is_file())
    rms: list[np.ndarray] = []
    kurt: list[np.ndarray] = []
    shapes: set[tuple[int, ...]] = set()
    for f in files:
        x = np.loadtxt(f, dtype=np.float64)
        shapes.add(x.shape)
        rms.append(np.sqrt((x**2).mean(axis=0)))
        kurt.append(np.asarray(kurtosis(x, axis=0, fisher=False)))
    rms_a, kurt_a = np.array(rms), np.array(kurt)
    n = len(files)
    k = args.failing_channel
    healthy = [c for c in range(rms_a.shape[1]) if c != k]
    early, late = slice(0, max(1, n // 10)), slice(n - max(1, n // 10), n)

    report = {
        "n_snapshots": n,
        "first_file": files[0].name,
        "last_file": files[-1].name,
        "snapshot_shapes": sorted(list(s) for s in shapes),
        "failing_channel_0based": k,
        "rms_early_mean_per_channel": rms_a[early].mean(axis=0).round(4).tolist(),
        "rms_late_mean_per_channel": rms_a[late].mean(axis=0).round(4).tolist(),
        "kurtosis_early_mean_per_channel": kurt_a[early].mean(axis=0).round(3).tolist(),
        "kurtosis_late_mean_per_channel": kurt_a[late].mean(axis=0).round(3).tolist(),
        "failing_rms_late_over_early": float(rms_a[late, k].mean() / rms_a[early, k].mean()),
        "healthy_rms_late_over_early": [
            float(rms_a[late, c].mean() / rms_a[early, c].mean()) for c in healthy
        ],
        "failing_rms_max_snapshot_index": int(rms_a[:, k].argmax()),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
