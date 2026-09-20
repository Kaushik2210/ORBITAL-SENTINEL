"""Probe the NASA DONKI space-weather API and print a JSON report (schema, counts, rate limits).

Uses ``NASA_API_KEY`` from the environment, falling back to ``DEMO_KEY``. Makes one request per
endpoint. Statistics in docs/DATASETS.md come from this output.
Usage: uv run python scripts/inspect_donki.py [--start 2024-05-01 --end 2024-05-31]
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx

BASE = "https://api.nasa.gov/DONKI"
ENDPOINTS = ("FLR", "CME", "GST", "SEP", "IPS")
TIME_FIELDS = {
    "FLR": "peakTime",
    "CME": "startTime",
    "GST": "startTime",
    "SEP": "eventTime",
    "IPS": "eventTime",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-05-01")
    ap.add_argument("--end", default="2024-05-31")
    args = ap.parse_args()
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")

    report: dict[str, Any] = {
        "window": [args.start, args.end],
        "key_kind": "demo" if key == "DEMO_KEY" else "user",
    }
    with httpx.Client(timeout=60) as client:
        for ep in ENDPOINTS:
            r = client.get(
                f"{BASE}/{ep}",
                params={"startDate": args.start, "endDate": args.end, "api_key": key},
            )
            entry: dict[str, Any] = {
                "status": r.status_code,
                "ratelimit_limit": r.headers.get("x-ratelimit-limit"),
                "ratelimit_remaining": r.headers.get("x-ratelimit-remaining"),
            }
            if r.status_code == 200:
                body = r.json()
                entry["count"] = len(body)
                if body:
                    entry["fields"] = sorted(body[0].keys())
                    tf = TIME_FIELDS[ep]
                    times = sorted(e[tf] for e in body if e.get(tf))
                    entry["time_field"] = tf
                    entry["first_time"], entry["last_time"] = times[0], times[-1]
                    if ep == "FLR":
                        entry["class_types"] = sorted({e.get("classType", "?") for e in body})
                    if ep == "GST":
                        kps = [k["kpIndex"] for e in body for k in e.get("allKpIndex", [])]
                        entry["max_kp"] = max(kps) if kps else None
            report[ep] = entry
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
