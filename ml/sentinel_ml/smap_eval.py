"""Event-level evaluation of the L1 statistical detectors on the real SMAP/MSL anomalies.

Per labeled channel: calibrate on the *train* array, score the *test* array, and compare predicted
anomalous runs with the labeled sequences using the Telemanom-style event rules:

* a labeled sequence is **detected** if any predicted run overlaps it (true positive event);
* a predicted run that overlaps no labeled sequence is a **false positive** event;
* an undetected labeled sequence is a **false negative** event.

Dataset quirks (docs/DATASETS.md): T-10 (no label row) is excluded; P-2 uses the union of its two
label rows; channels with a constant training signal are reported separately because a
variance-based detector has nothing to fit there. L1 only: the LSTM forecaster (L2), which would use
the command columns, is not built yet.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from sentinel_core.detection.statistical import StatisticalDetector
from sentinel_core.events import TelemetryEvent
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import smap_msl

GAP = 10  # fired steps closer than this are merged into one predicted run
ROOT = Path("data/raw")


def _runs(steps: list[int], gap: int = GAP) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s in sorted(set(steps)):
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], s)
        else:
            out.append((s, s))
    return out


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def event_counts(truth: list[tuple[int, int]], pred: list[tuple[int, int]]) -> tuple[int, int]:
    """(detected labeled events, false-positive predicted runs) under the event-level rules."""
    tp = sum(1 for t in truth if any(_overlap(t, p) for p in pred))
    fp = sum(1 for p in pred if not any(_overlap(t, p) for t in truth))
    return tp, fp


def l1_fired_steps(root: Path, channel: str) -> list[int]:
    """Test-array steps at which the calibrated L1 detector fires (used to combine with L2)."""
    labels = smap_msl.load_labels(root)
    series = smap_msl.load_channel(root, channel, labels)
    det = StatisticalDetector()
    for k, v in enumerate(series.train):
        det.learn(TelemetryEvent(k * STEP_SECONDS, channel, float(v), ts_rx=k * STEP_SECONDS))
    det.freeze()
    fired: list[int] = []
    for k, v in enumerate(series.test):
        ts = k * STEP_SECONDS
        if any(o.fired for o in det.update(TelemetryEvent(ts, channel, float(v), ts_rx=ts))):
            fired.append(k)
    return fired


def score_channel(root: Path, channel: str) -> dict[str, Any]:
    labels = smap_msl.load_labels(root)
    series = smap_msl.load_channel(root, channel, labels)
    lab = labels[channel]
    det = StatisticalDetector()
    for k, v in enumerate(series.train):
        det.learn(TelemetryEvent(k * STEP_SECONDS, channel, float(v), ts_rx=k * STEP_SECONDS))
    det.freeze()
    fired_by_sub: dict[str, list[int]] = {}
    fired_all: list[int] = []
    for k, v in enumerate(series.test):
        ts = k * STEP_SECONDS
        for o in det.update(TelemetryEvent(ts, channel, float(v), ts_rx=ts)):
            if o.fired:
                sub = o.detector.rsplit(".", 1)[-1]
                fired_by_sub.setdefault(sub, []).append(k)
                fired_all.append(k)
    truth = list(lab.intervals)
    pred = _runs(fired_all)
    tp = sum(1 for t in truth if any(_overlap(t, p) for p in pred))
    fp = sum(1 for p in pred if not any(_overlap(t, p) for t in truth))
    per_sub = {
        sub: sum(1 for t in truth if any(_overlap(t, p) for p in _runs(steps)))
        for sub, steps in fired_by_sub.items()
    }
    mask = lab.mask(len(series.test))
    return {
        "channel": channel,
        "spacecraft": lab.spacecraft,
        "constant_train": bool(np.ptp(series.train) == 0),
        "label_rows": lab.n_label_rows,
        "true_events": len(truth),
        "detected_events": tp,
        "predicted_runs": len(pred),
        "false_positive_runs": fp,
        "test_len": len(series.test),
        "anomalous_points": int(mask.sum()),
        "fired_points_outside_labels": int(sum(1 for k in set(fired_all) if not mask[k])),
        "detected_by_statistic": per_sub,
    }


_WORKER_ROOT = ROOT


def _init(root: str) -> None:
    global _WORKER_ROOT
    _WORKER_ROOT = Path(root)


def _one(channel: str) -> dict[str, Any]:
    return score_channel(_WORKER_ROOT, channel)


def prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else (0.0 if tp + fn else None)
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(r["detected_events"] for r in rows)
    fn = sum(r["true_events"] - r["detected_events"] for r in rows)
    fp = sum(r["false_positive_runs"] for r in rows)
    stat: Counter[str] = Counter()
    for r in rows:
        stat.update(r["detected_by_statistic"])
    return {
        "channels": len(rows),
        "true_events": tp + fn,
        **prf(tp, fp, fn),
        "events_detected_by_statistic": dict(stat),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=Path("docs/data/smap_msl_l1_v1.json"))
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    labels = smap_msl.load_labels(args.root)
    chans = sorted(labels)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(str(args.root),)) as pool:
        rows = list(pool.map(_one, chans))
    wall = time.time() - t0
    varying = [r for r in rows if not r["constant_train"]]
    report: dict[str, Any] = {
        "meta": {
            "detector": "l1.statistical (calibrated on the train array, scored on the test array)",
            "gap_merge_steps": GAP,
            "excluded": ["T-10 (no label row)"],
            "p2_note": "P-2 scored against the union of its two label rows",
            "wall_seconds": wall,
            "layers_not_evaluated": (
                "L2 forecaster (not built yet); L3/L4/L5 do not apply to anonymized channels"
            ),
        },
        "all": summarize(rows),
        "SMAP": summarize([r for r in rows if r["spacecraft"] == "SMAP"]),
        "MSL": summarize([r for r in rows if r["spacecraft"] == "MSL"]),
        "constant_train_channels": {
            "n": len(rows) - len(varying),
            "channels": [r["channel"] for r in rows if r["constant_train"]],
            **summarize([r for r in rows if r["constant_train"]]),
        },
        "varying_train_channels": summarize(varying),
        "channels": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    a = report["all"]
    print(
        f"{a['channels']} channels, {a['true_events']} events: P={a['precision']:.3f} "
        f"R={a['recall']:.3f} F1={a['f1']:.3f} ({wall:.0f}s)"
    )


if __name__ == "__main__":
    main()
