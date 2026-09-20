"""Inspect the real SMAP/MSL arrays and labels; print a JSON report.

Every SMAP/MSL statistic quoted in docs/DATASETS.md comes from this script's output.
Usage: uv run python scripts/inspect_smap_msl.py [--root data/raw] > report.json
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
from pathlib import Path

import numpy as np


def load_labels(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                {
                    "chan_id": r["chan_id"],
                    "spacecraft": r["spacecraft"],
                    "sequences": ast.literal_eval(r["anomaly_sequences"]),
                    "classes": [c.strip() for c in r["class"].strip("[]").split(",")],
                    "num_values": int(r["num_values"]),
                }
            )
    return rows


def command_columns_report(arr: np.ndarray) -> dict[str, object]:
    cmd = arr[:, 1:]
    binary = bool(np.isin(cmd, (0.0, 1.0)).all())
    row_sums = cmd.sum(axis=1)
    return {
        "binary": binary,
        "rows_with_0_active": int((row_sums == 0).sum()),
        "rows_with_1_active": int((row_sums == 1).sum()),
        "rows_with_gt1_active": int((row_sums > 1).sum()),
        "active_frac": float((cmd > 0).mean()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("data/raw"))
    root = ap.parse_args().root / "smap_msl"

    labels = load_labels(root / "labeled_anomalies.csv")
    by_chan: dict[str, list[dict[str, object]]] = {}
    for row in labels:
        by_chan.setdefault(str(row["chan_id"]), []).append(row)
    spacecraft_of = {str(r["chan_id"]): str(r["spacecraft"]) for r in labels}

    channels = sorted(p.stem for p in (root / "test").glob("*.npy"))
    per_chan: dict[str, dict[str, object]] = {}
    nonbinary_cmd: list[str] = []
    for ch in channels:
        tr = np.load(root / "train" / f"{ch}.npy")
        te = np.load(root / "test" / f"{ch}.npy")
        rep: dict[str, object] = {
            "spacecraft": spacecraft_of.get(ch),
            "train_shape": list(tr.shape),
            "test_shape": list(te.shape),
            "dtype": str(tr.dtype),
            "train_f0_min": float(tr[:, 0].min()),
            "train_f0_max": float(tr[:, 0].max()),
            "test_f0_min": float(te[:, 0].min()),
            "test_f0_max": float(te[:, 0].max()),
            "f0_unique_train": int(np.unique(tr[:, 0]).size),
            "nan_or_inf": int((~np.isfinite(tr)).sum() + (~np.isfinite(te)).sum()),
            "cmd_test": command_columns_report(te) if te.shape[1] > 1 else None,
        }
        cmd_rep = rep["cmd_test"]
        if isinstance(cmd_rep, dict) and not cmd_rep["binary"]:
            nonbinary_cmd.append(ch)
        rows = by_chan.get(ch)
        if rows:
            mask = np.zeros(te.shape[0], dtype=bool)
            n_seq = 0
            in_bounds = True
            for r in rows:
                for a, b in r["sequences"]:  # type: ignore[attr-defined]
                    n_seq += 1
                    in_bounds &= 0 <= a <= b <= te.shape[0]
                    mask[a : b + 1] = True
            rep.update(
                label_rows=len(rows),
                n_sequences=n_seq,
                anomalous_points=int(mask.sum()),
                anomalous_frac=float(mask.mean()),
                num_values_matches_test_len=[r["num_values"] == te.shape[0] for r in rows],
                intervals_in_bounds=bool(in_bounds),
            )
        else:
            rep["label_rows"] = 0
        per_chan[ch] = rep

    def agg(sc: str) -> dict[str, object]:
        cs = [c for c, r in per_chan.items() if r["spacecraft"] == sc]
        nfeat = sorted({r["train_shape"][1] for c, r in per_chan.items() if c in cs})  # type: ignore[index]
        return {
            "channels": len(cs),
            "n_features": nfeat,
            "train_points": sum(per_chan[c]["train_shape"][0] for c in cs),  # type: ignore[index]
            "test_points": sum(per_chan[c]["test_shape"][0] for c in cs),  # type: ignore[index]
        }

    seq_lengths = [
        b - a + 1
        for r in labels
        for a, b in r["sequences"]  # type: ignore[attr-defined]
    ]
    class_counts: dict[str, int] = {}
    for r in labels:
        for c in r["classes"]:  # type: ignore[attr-defined]
            class_counts[c] = class_counts.get(c, 0) + 1

    report = {
        "n_channel_arrays": len(channels),
        "channels_without_label_row": [c for c in channels if c not in by_chan],
        "channels_with_multiple_label_rows": {c: len(v) for c, v in by_chan.items() if len(v) > 1},
        "label_rows": len(labels),
        "unique_label_channels": len(by_chan),
        "total_anomaly_sequences": len(seq_lengths),
        "sequence_length_min_median_max": [
            int(min(seq_lengths)),
            float(np.median(seq_lengths)),
            int(max(seq_lengths)),
        ],
        "sequence_class_counts": class_counts,
        "nonbinary_command_columns_in": nonbinary_cmd,
        "channels_with_nan_or_inf": [c for c, r in per_chan.items() if r["nan_or_inf"]],
        "SMAP": agg("SMAP"),
        "MSL": agg("MSL"),
        "per_channel": per_chan,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
