"""Train and evaluate the L2 forecaster on the real SMAP/MSL data (event level), with baselines.

Per labeled channel, all methods see only the *train* array to learn and are scored on the *test*
array with the same event-level rules as ``smap_eval``:

* **L1**  the calibrated statistical detector (already evaluated);
* **L2**  LSTM forecaster + EWMA + nonparametric dynamic thresholding + pruning;
* **IF**  an Isolation Forest baseline on [value, delta, rolling mean, rolling std];
* **L1+L2** the union of L1's fired steps and L2's anomalous sequences.

``T-10`` (no label row) is excluded; ``P-2`` uses the union of its label rows. Training uses a
15-epoch cap (Telemanom: 35) for CPU time; this is a documented deviation.
"""

from __future__ import annotations

import argparse
import json
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import IsolationForest

from sentinel_core.detection.dynamic_threshold import anomalous_sequences, merge
from sentinel_sim import smap_msl

from . import forecaster as fc
from .smap_eval import GAP, _runs, event_counts, l1_fired_steps, prf

METHODS = ("L1", "L2", "IF", "L1+L2")
_ROOT = Path("data/raw")
_OUT = Path("ml/runs/l2-v1")


def _init(root: str, out: str) -> None:
    import torch

    global _ROOT, _OUT
    _ROOT, _OUT = Path(root), Path(out)
    torch.set_num_threads(2)


def _if_features(v: NDArray[np.float64], w: int = 10) -> NDArray[np.float64]:
    d = np.diff(v, prepend=v[0])
    pad = np.concatenate([np.full(w - 1, v[0]), v])
    win = np.lib.stride_tricks.sliding_window_view(pad, w)
    return np.column_stack([v, d, win.mean(axis=1), win.std(axis=1)])


def isolation_forest_runs(
    train: NDArray[np.float64], test: NDArray[np.float64], seed: int
) -> list[tuple[int, int]]:
    forest = IsolationForest(n_estimators=100, random_state=seed).fit(_if_features(train))
    thr = float(np.percentile(forest.decision_function(_if_features(train)), 0.5))
    flagged = np.flatnonzero(forest.decision_function(_if_features(test)) < thr)
    return _runs(flagged.tolist())


def channel_job(channel: str) -> dict[str, Any]:
    labels = smap_msl.load_labels(_ROOT)
    s = smap_msl.load_channel(_ROOT, channel, labels)
    truth = list(labels[channel].intervals)
    seed = zlib.crc32(channel.encode())
    tc = smap_msl.unpack_commands(s.train_cmd, s.n_cmd)
    ec = smap_msl.unpack_commands(s.test_cmd, s.n_cmd)

    t0 = time.time()
    res = fc.train_channel(s.train, tc, seed=seed)
    train_s = time.time() - t0
    onnx_path = _OUT / "onnx" / f"{channel}.onnx"
    fc.export_onnx(res.model, res.n_in, onnx_path)
    x = fc.windows(fc.features(s.test, ec))[:64]
    import torch

    with torch.no_grad():
        ref = res.model(torch.from_numpy(x)).numpy()
    parity = (
        float(np.abs(fc.onnx_predict(fc.onnx_session(onnx_path), x) - ref).max()) if len(x) else 0.0
    )

    errors = fc.smoothed_errors(s.test, fc.predict(res.model, s.test, ec))
    l2_seqs = merge(anomalous_sequences(errors), gap=GAP)
    l1_steps = l1_fired_steps(_ROOT, channel)
    l2_steps = [k for a, b in l2_seqs for k in range(a, b + 1)]
    preds = {
        "L1": _runs(l1_steps),
        "L2": l2_seqs,
        "IF": isolation_forest_runs(s.train, s.test, seed),
        "L1+L2": _runs(l1_steps + l2_steps),
    }
    counts = {m: event_counts(truth, runs) for m, runs in preds.items()}
    return {
        "channel": channel,
        "spacecraft": labels[channel].spacecraft,
        "constant_train": bool(np.ptp(s.train) == 0),
        "true_events": len(truth),
        "train_seconds": train_s,
        "epochs": res.epochs,
        "train_mse": res.train_loss,
        "val_mse": res.val_loss,
        "onnx_parity_max_abs_diff": parity,
        "results": {
            m: {"tp": tp, "fp": fp, "runs": len(preds[m])} for m, (tp, fp) in counts.items()
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "channels": len(rows),
        "true_events": sum(r["true_events"] for r in rows),
    }
    for m in METHODS:
        tp = sum(r["results"][m]["tp"] for r in rows)
        fp = sum(r["results"][m]["fp"] for r in rows)
        out[m] = prf(tp, fp, out["true_events"] - tp)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("ml/runs/l2-v1"))
    ap.add_argument("--report", type=Path, default=Path("docs/data/smap_msl_l2_v1.json"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--channels", default="", help="comma-separated subset (default: all labeled)")
    args = ap.parse_args()
    labels = smap_msl.load_labels(args.root)
    chans = [c for c in args.channels.split(",") if c] or sorted(labels)
    t0 = time.time()
    with ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(str(args.root), str(args.out))
    ) as pool:
        rows = list(pool.map(channel_job, chans))
    varying = [r for r in rows if not r["constant_train"]]
    report: dict[str, Any] = {
        "meta": {
            "methods": list(METHODS),
            "window": fc.WINDOW,
            "hidden": fc.HIDDEN,
            "layers": fc.LAYERS,
            "smooth_span": fc.SMOOTH_SPAN,
            "max_epochs": 15,
            "note": "Telemanom trains up to 35 epochs; capped at 15 for CPU time.",
            "wall_seconds": time.time() - t0,
            "max_onnx_parity_diff": max(r["onnx_parity_max_abs_diff"] for r in rows),
        },
        "all": summarize(rows),
        "SMAP": summarize([r for r in rows if r["spacecraft"] == "SMAP"]),
        "MSL": summarize([r for r in rows if r["spacecraft"] == "MSL"]),
        "varying_train_channels": summarize(varying),
        "constant_train_channels": summarize([r for r in rows if r["constant_train"]]),
        "channels": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    v = report["varying_train_channels"]
    print(
        f"{len(rows)} channels in {report['meta']['wall_seconds']:.0f}s | varying-signal F1: "
        + ", ".join(f"{m} {v[m]['f1']:.2f}" for m in METHODS)
    )


if __name__ == "__main__":
    main()
