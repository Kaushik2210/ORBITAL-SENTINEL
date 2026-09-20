"""Inspect the NASA PCoE Li-ion battery aging archive; print a JSON report.

Reads the nested zips in memory (the raw archive is never modified). Every battery statistic in
docs/DATASETS.md comes from this script's output.
Usage: uv run python scripts/inspect_pcoe_battery.py [--root data/raw] > report.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat


def iter_mats(outer_zip: Path) -> list[tuple[str, str, bytes]]:
    """Return (inner_archive_name, mat_name, bytes) for every .mat in the nested zips."""
    out: list[tuple[str, str, bytes]] = []
    with zipfile.ZipFile(outer_zip) as outer:
        for name in outer.namelist():
            if not name.endswith(".zip"):
                continue
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                out.extend(
                    (Path(name).name, m, inner.read(m))
                    for m in inner.namelist()
                    if m.endswith(".mat")
                )
    return out


def _scalar_capacity(value: Any) -> float | None:
    """Capacity is one number per discharge cycle, but some cycles hold an empty/array value."""
    arr = np.atleast_1d(np.asarray(value, dtype=float)).ravel()
    return float(arr[0]) if arr.size == 1 else None


def summarize(mat_bytes: bytes, key: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / f"{key}.mat"
        p.write_bytes(mat_bytes)
        data = loadmat(str(p), squeeze_me=True, struct_as_record=False)
    cycles = data[key].cycle
    types: dict[str, int] = {}
    for c in cycles:
        types[c.type] = types.get(c.type, 0) + 1
    raw_caps = [_scalar_capacity(c.data.Capacity) for c in cycles if c.type == "discharge"]
    caps = [x for x in raw_caps if x is not None]
    temps = sorted({int(c.ambient_temperature) for c in cycles})
    return {
        "cycles": len(cycles),
        "cycle_types": types,
        "discharge_capacity_first_ah": caps[0] if caps else None,
        "discharge_capacity_last_ah": caps[-1] if caps else None,
        "capacity_fade_pct": (
            round(100 * (caps[0] - caps[-1]) / caps[0], 1) if len(caps) > 1 else None
        ),
        "discharge_cycles_without_scalar_capacity": len(raw_caps) - len(caps),
        "ambient_temperatures_c": temps,
        "min_capacity_ah": float(np.min(caps)) if caps else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("data/raw"))
    zpath = ap.parse_args().root / "pcoe_battery" / "5.+Battery+Data+Set.zip"

    batteries: dict[str, dict[str, Any]] = {}
    digests: dict[str, list[str]] = {}
    for archive, mat_name, blob in iter_mats(zpath):
        key = Path(mat_name).stem
        digests.setdefault(key, []).append(hashlib.sha256(blob).hexdigest())
        if key not in batteries:
            batteries[key] = {"archive": archive, **summarize(blob, key)}
    faded = [
        b["capacity_fade_pct"] for b in batteries.values() if b["capacity_fade_pct"] is not None
    ]
    report = {
        "n_batteries": len(batteries),
        "ids_in_multiple_archives_identical_bytes": sorted(
            k for k, v in digests.items() if len(v) > 1 and len(set(v)) == 1
        ),
        "ids_in_multiple_archives_different_bytes": sorted(
            k for k, v in digests.items() if len(set(v)) > 1
        ),
        "capacity_fade_pct_min_median_max": [min(faded), float(np.median(faded)), max(faded)],
        "batteries": batteries,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
