"""NASA DONKI space-weather client with a permanent on-disk cache and rate-limit handling.

DEMO_KEY reported ``X-RateLimit-Limit: 10`` when probed (docs/DATASETS.md), so every response is
cached forever, requests span at most 30 days, and a 429 raises :class:`RateLimitedError` instead
of retrying in a tight loop. A cache miss with no network yields "no context", never a
fabricated "no event": callers must distinguish the two via :meth:`DonkiClient.has_coverage`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from sentinel_core.events import WeatherEvent

BASE_URL = "https://api.nasa.gov/DONKI"
KINDS = ("FLR", "CME", "GST", "SEP", "IPS")
MAX_WINDOW_DAYS = 30
_TIME_FIELDS = {
    "FLR": "peakTime",
    "CME": "startTime",
    "GST": "startTime",
    "SEP": "eventTime",
    "IPS": "eventTime",
}
_FLARE_RE = re.compile(r"^([ABCMX])(\d+(?:\.\d+)?)$")
_FLARE_BASE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}  # W/m^2 (GOES class)


class RateLimitedError(RuntimeError):
    def __init__(self, retry_after_s: float | None) -> None:
        super().__init__(f"DONKI rate limited (retry after {retry_after_s}s)")
        self.retry_after_s = retry_after_s


@dataclass(frozen=True, slots=True)
class CachedWindow:
    kind: str
    start: date
    end: date
    fetched_at: str
    key_kind: str  # "demo" or "user"; never the key itself
    events: list[dict[str, Any]]


def _windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def _within(win: CachedWindow, start: date, end: date) -> CachedWindow:
    """Restrict a cached window to events whose time falls inside [start, end] (UTC dates)."""
    tf = _TIME_FIELDS[win.kind]
    keep = [e for e in win.events if e.get(tf) and start <= parse_time(e[tf]).date() <= end]
    return CachedWindow(win.kind, start, end, win.fetched_at, win.key_kind, keep)


def parse_time(text: str) -> datetime:
    """DONKI times look like ``2024-05-01T06:57Z``."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def flare_flux(class_type: str) -> float | None:
    """GOES class like ``X8.7`` -> peak flux in W/m^2, or None if unparseable."""
    m = _FLARE_RE.match(class_type.strip())
    return float(m.group(2)) * _FLARE_BASE[m.group(1)] if m else None


class DonkiClient:
    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.api_key = api_key or os.environ.get("NASA_API_KEY") or "DEMO_KEY"
        self._client = client or httpx.Client(timeout=60.0)
        self.requests_made = 0

    @property
    def key_kind(self) -> str:
        return "demo" if self.api_key == "DEMO_KEY" else "user"

    def _path(self, kind: str, start: date, end: date) -> Path:
        return self.cache_dir / f"{kind}_{start.isoformat()}_{end.isoformat()}.json"

    def _covering(self, kind: str, start: date, end: date) -> CachedWindow | None:
        """A cached window that fully contains [start, end], if any (any cached span works)."""
        for path in sorted(self.cache_dir.glob(f"{kind}_*.json")):
            try:
                _, a, b = path.stem.split("_")
                wa, wb = date.fromisoformat(a), date.fromisoformat(b)
            except ValueError:
                continue
            if wa <= start and end <= wb:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return CachedWindow(kind, wa, wb, raw["fetched_at"], raw["key_kind"], raw["events"])
        return None

    def has_coverage(self, kind: str, start: date, end: date) -> bool:
        """True if [start, end] is cached: as exact 30-day windows or inside a cached span."""
        if self._covering(kind, start, end) is not None:
            return True
        return all(self._path(kind, a, b).is_file() for a, b in _windows(start, end))

    def _fetch(self, kind: str, start: date, end: date) -> CachedWindow:
        resp = self._client.get(
            f"{BASE_URL}/{kind}",
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "api_key": self.api_key,
            },
        )
        self.requests_made += 1
        if resp.status_code == 429:
            retry = resp.headers.get("retry-after")
            raise RateLimitedError(float(retry) if retry and retry.isdigit() else None)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list):  # the API returns [] for no events
            raise ValueError(f"unexpected DONKI response for {kind}: {type(body).__name__}")
        return CachedWindow(kind, start, end, datetime.now(UTC).isoformat(), self.key_kind, body)

    def get_window(
        self, kind: str, start: date, end: date, *, allow_network: bool = True
    ) -> CachedWindow | None:
        """Events for one <=30-day window from cache or network; None if uncached and offline."""
        path = self._path(kind, start, end)
        if not path.is_file() and (cover := self._covering(kind, start, end)) is not None:
            return _within(cover, start, end)
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return CachedWindow(kind, start, end, raw["fetched_at"], raw["key_kind"], raw["events"])
        if not allow_network:
            return None
        win = self._fetch(kind, start, end)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(
            json.dumps(
                {"fetched_at": win.fetched_at, "key_kind": win.key_kind, "events": win.events}
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
        return win

    def get_events(
        self, kind: str, start: date, end: date, *, allow_network: bool = True
    ) -> list[dict[str, Any]]:
        """All events of ``kind`` in [start, end]. Raises if any window is uncached and offline."""
        if kind not in KINDS:
            raise ValueError(f"unknown DONKI kind {kind!r}")
        out: list[dict[str, Any]] = []
        for a, b in _windows(start, end):
            win = self.get_window(kind, a, b, allow_network=allow_network)
            if win is None:
                raise LookupError(f"{kind} {a}..{b} not cached and network disabled")
            out.extend(win.events)
        return out


def to_weather_events(
    kind: str, events: list[dict[str, Any]], epoch: datetime
) -> list[WeatherEvent]:
    """Map raw DONKI events to mission-time :class:`WeatherEvent`s (seconds since ``epoch``).

    Flares span begin..end; the other kinds are instantaneous, so they get a nominal 1-hour extent
    around the event time. Magnitude: flare peak flux (W/m^2), storm max Kp, else None.
    """
    tf = _TIME_FIELDS[kind]
    out: list[WeatherEvent] = []
    for e in events:
        if not e.get(tf):
            continue
        t0 = parse_time(e["beginTime"] if kind == "FLR" and e.get("beginTime") else e[tf])
        t1 = (
            parse_time(e["endTime"])
            if kind == "FLR" and e.get("endTime")
            else t0 + timedelta(hours=1)
        )
        mag: float | None = None
        if kind == "FLR":
            mag = flare_flux(str(e.get("classType", "")))
        elif kind == "GST":
            kps = [k["kpIndex"] for k in e.get("allKpIndex", []) if "kpIndex" in k]
            mag = max(kps) if kps else None
        out.append(
            WeatherEvent(kind, (t0 - epoch).total_seconds(), (t1 - epoch).total_seconds(), mag)
        )
    return sorted(out, key=lambda w: w.begin_ts)
