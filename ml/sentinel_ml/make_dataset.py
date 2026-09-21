"""Generate the labeled attribution dataset by running every scenario variant through the pipeline.

Usage: ``uv run python -m sentinel_ml.make_dataset --variants 14 --out data/processed/records.pkl``.
Splits are by variant index (train 0-7, validation 8-9, test 10-13): every scenario family appears
in every split, with different seeds, timing and magnitudes. See ml/sentinel_ml/splits.py.
"""

from __future__ import annotations

import argparse
import pickle
import time
from pathlib import Path

from sentinel_sim.scenarios.spec import load_all

from .batch import run_suite


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variants", type=int, default=14)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--scenarios", type=Path, default=Path("data/scenarios"))
    ap.add_argument("--data-root", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/processed/records.pkl"))
    args = ap.parse_args()
    specs = load_all(args.scenarios)
    t0 = time.time()
    records = run_suite(specs, args.variants, args.data_root, args.workers, keep_windows=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as fh:
        pickle.dump(records, fh)
    n_samples = sum(len(r.samples) for r in records)
    print(
        f"{len(records)} runs, {n_samples} incident windows, {time.time() - t0:.0f} s -> {args.out}"
    )


if __name__ == "__main__":
    main()
