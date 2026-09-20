"""CLI: ``python -m sentinel_sim.datasets --profile lite|full`` (wrapped by ``make data``)."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

from .fetch import FetchError, make_client
from .manifest import FileRecord, Manifest
from .sources import fetch_pcoe_battery, fetch_pcoe_bearings, fetch_smap_msl

Fetcher = Callable[[httpx.Client, Path, Manifest], list[FileRecord]]

PROFILES: dict[str, dict[str, Fetcher]] = {
    "lite": {"smap_msl": fetch_smap_msl, "pcoe_battery": fetch_pcoe_battery},
    "full": {
        "smap_msl": fetch_smap_msl,
        "pcoe_battery": fetch_pcoe_battery,
        "pcoe_bearings": fetch_pcoe_bearings,
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sentinel_sim.datasets", description=__doc__)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="lite")
    ap.add_argument("--root", type=Path, default=Path("data/raw"))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx logs request URLs at INFO, including signed CDN redirects: keep those out of logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    manifest = Manifest.load(args.root)
    failures: list[str] = []
    with make_client() as client:
        for name, fetcher in PROFILES[args.profile].items():
            try:
                recs = fetcher(client, args.root, manifest)
            except FetchError as exc:
                failures.append(name)
                print(f"[FAIL] {name}: {exc}", file=sys.stderr)
            else:
                mb = sum(r.size_bytes for r in recs) / 1e6
                srcs = sorted({r.source for r in recs})
                print(f"[ ok ] {name}: {len(recs)} files, {mb:.1f} MB, sources={srcs}")
            manifest.save(args.root)  # persist progress after each dataset
    if failures:
        print(
            f"datasets unavailable: {failures}. The synthetic fallback generator (documented in "
            "docs/DATASETS.md) is used for these; results are labeled synthetic.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
