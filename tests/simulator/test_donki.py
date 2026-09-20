from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from sentinel_sim import donki

FLR: list[dict[str, Any]] = [
    {
        "flrID": "a",
        "classType": "X8.7",
        "beginTime": "2024-05-14T16:46Z",
        "peakTime": "2024-05-14T16:51Z",
        "endTime": "2024-05-14T16:55Z",
    },
    {
        "flrID": "b",
        "classType": "C4.8",
        "beginTime": "2024-05-01T06:50Z",
        "peakTime": "2024-05-01T06:57Z",
        "endTime": None,
    },
    {"flrID": "no-peak"},
]


def make_client(
    body: object = FLR, status: int = 200, headers: dict[str, str] | None = None
) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_second_call_is_served_from_disk_cache(tmp_path: Path) -> None:
    client, seen = make_client()
    d = donki.DonkiClient(tmp_path, client=client)
    a = d.get_events("FLR", date(2024, 5, 1), date(2024, 5, 30))  # exactly one 30-day window
    b = d.get_events("FLR", date(2024, 5, 1), date(2024, 5, 30))
    assert a == b == FLR
    assert len(seen) == 1
    assert d.has_coverage("FLR", date(2024, 5, 1), date(2024, 5, 30))
    assert not d.has_coverage("FLR", date(2024, 5, 1), date(2024, 5, 31))  # 31st needs a 2nd window


def test_long_ranges_are_split_into_30_day_windows(tmp_path: Path) -> None:
    client, seen = make_client(body=[])
    donki.DonkiClient(tmp_path, client=client).get_events(
        "CME", date(2024, 1, 1), date(2024, 3, 15)
    )
    spans = [(r.url.params["startDate"], r.url.params["endDate"]) for r in seen]
    assert spans == [
        ("2024-01-01", "2024-01-30"),
        ("2024-01-31", "2024-02-29"),
        ("2024-03-01", "2024-03-15"),
    ]


def test_offline_miss_raises_instead_of_pretending_there_are_no_events(tmp_path: Path) -> None:
    client, seen = make_client()
    d = donki.DonkiClient(tmp_path, client=client)
    with pytest.raises(LookupError, match="not cached"):
        d.get_events("FLR", date(2024, 5, 1), date(2024, 5, 2), allow_network=False)
    assert not seen
    assert not d.has_coverage("FLR", date(2024, 5, 1), date(2024, 5, 2))


def test_rate_limit_raises_typed_error_and_caches_nothing(tmp_path: Path) -> None:
    client, _ = make_client(body={"error": "over rate"}, status=429, headers={"retry-after": "120"})
    d = donki.DonkiClient(tmp_path, client=client)
    with pytest.raises(donki.RateLimitedError) as ei:
        d.get_events("FLR", date(2024, 5, 1), date(2024, 5, 2))
    assert ei.value.retry_after_s == 120.0
    assert not list(tmp_path.glob("*.json"))


def test_unexpected_payload_shape_is_rejected(tmp_path: Path) -> None:
    client, _ = make_client(body={"not": "a list"})
    with pytest.raises(ValueError, match="unexpected DONKI response"):
        donki.DonkiClient(tmp_path, client=client).get_events(
            "FLR", date(2024, 5, 1), date(2024, 5, 2)
        )


def test_unknown_kind_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown DONKI kind"):
        donki.DonkiClient(tmp_path).get_events("XYZ", date(2024, 5, 1), date(2024, 5, 2))


def test_api_key_never_written_to_cache(tmp_path: Path) -> None:
    client, _ = make_client()
    d = donki.DonkiClient(tmp_path, api_key="super-secret-key-123", client=client)
    d.get_events("FLR", date(2024, 5, 1), date(2024, 5, 31))
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "super-secret-key-123" not in text
    assert (
        json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))["key_kind"] == "user"
    )


def test_demo_key_is_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NASA_API_KEY", raising=False)
    assert donki.DonkiClient(tmp_path).key_kind == "demo"


@pytest.mark.parametrize(
    ("cls", "flux"),
    [
        ("X8.7", 8.7e-4),
        ("M1.0", 1e-5),
        ("C4.8", 4.8e-6),
        ("B2", 2e-7),
        ("garbage", None),
        ("", None),
    ],
)
def test_flare_flux(cls: str, flux: float | None) -> None:
    got = donki.flare_flux(cls)
    assert got == pytest.approx(flux) if flux is not None else got is None


def test_to_weather_events_maps_to_mission_time_and_skips_bad_rows() -> None:
    epoch = datetime(2024, 5, 14, 16, 0, tzinfo=UTC)
    ev = donki.to_weather_events("FLR", FLR, epoch)
    c = next(e for e in ev if e.magnitude and e.magnitude < 1e-5)
    assert c.begin_ts == pytest.approx(-(13 * 24 * 3600 + 9 * 3600 + 10 * 60))  # 05-01 06:50Z
    assert len(ev) == 2  # the row with no peakTime is dropped
    x = next(e for e in ev if e.magnitude and e.magnitude > 1e-4)
    assert x.begin_ts == pytest.approx(46 * 60)
    assert x.end_ts == pytest.approx(55 * 60)
    assert not x.synthetic
    assert ev == sorted(ev, key=lambda e: e.begin_ts)


def test_gst_uses_max_kp_and_one_hour_extent() -> None:
    epoch = datetime(2024, 5, 10, 0, 0, tzinfo=UTC)
    gst = [{"startTime": "2024-05-10T15:00Z", "allKpIndex": [{"kpIndex": 7.0}, {"kpIndex": 9.0}]}]
    (e,) = donki.to_weather_events("GST", gst, epoch)
    assert e.magnitude == 9.0
    assert e.end_ts - e.begin_ts == pytest.approx(3600)
