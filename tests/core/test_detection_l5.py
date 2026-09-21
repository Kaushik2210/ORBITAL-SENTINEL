from __future__ import annotations

from sentinel_core.detection.base import DetectorOutput, Layer
from sentinel_core.detection.environment import SimultaneousUpset, SpaceWeatherOverlap
from sentinel_core.events import WeatherEvent


def l1_out(ts: float, ch: str, sub: str = "zscore", fired: bool = True) -> DetectorOutput:
    return DetectorOutput(
        detector=f"l1.statistical.{sub}", layer=Layer.L1, ts=ts, channel=ch,
        score=0.9 if fired else 0.4, fired=fired, explanation="x",
    )  # fmt: skip


def group(ch: str) -> str:
    return "EPS" if ch.startswith("batt") or ch in ("bus_v", "solar_i") else "ADCS"


def overlap(kind: str = "SEP") -> SpaceWeatherOverlap:
    d = SpaceWeatherOverlap()
    d.update(WeatherEvent(kind, 10_000.0, 10_600.0, 8e-5))
    return d


def test_anomaly_during_a_real_event_is_flagged_with_the_event_as_evidence() -> None:
    (o,) = overlap().observe(l1_out(10_300.0, "batt_v_a"))
    assert o.fired
    assert o.layer is Layer.L5
    assert {e.name: e.observed for e in o.evidence}["event_kind"] == "SEP"


def test_particle_events_score_higher_than_flares() -> None:
    (sep,) = overlap("SEP").observe(l1_out(10_300.0, "a"))
    (flr,) = overlap("FLR").observe(l1_out(10_300.0, "a"))
    assert sep.score > flr.score


def test_no_overlap_outside_the_event_window_or_for_unfired_outputs() -> None:
    d = overlap()
    assert d.observe(l1_out(1_000.0, "a")) == []  # long before
    assert d.observe(l1_out(10_000.0 + 8 * 3600, "a")) == []  # beyond the 6 h lag
    assert d.observe(l1_out(10_300.0, "a", fired=False)) == []


def test_the_effect_may_slightly_precede_the_cataloged_onset() -> None:
    assert len(overlap().observe(l1_out(10_000.0 - 1200.0, "a"))) == 1


def test_repeat_reports_for_one_event_are_throttled() -> None:
    d = overlap()
    assert len(d.observe(l1_out(10_300.0, "a"))) == 1
    assert d.observe(l1_out(10_360.0, "b")) == []
    assert len(d.observe(l1_out(10_300.0 + 1900.0, "a"))) == 1


def test_reset_forgets_events() -> None:
    d = overlap()
    d.reset()
    assert d.observe(l1_out(10_300.0, "a")) == []


def test_simultaneous_upset_needs_three_channels_in_two_subsystems() -> None:
    d = SimultaneousUpset(group)
    assert d.observe(l1_out(600.0, "batt_v_a")) == []
    assert d.observe(l1_out(600.0, "bus_v")) == []
    assert d.observe(l1_out(600.0, "solar_i")) == []  # 3 channels but only one subsystem
    (o,) = d.observe(l1_out(600.0, "rw2_temp"))
    assert o.fired
    assert {e.name: e.observed for e in o.evidence}["channels_at_once"] == 4.0


def test_simultaneous_upset_ignores_slow_statistics_and_other_steps() -> None:
    d = SimultaneousUpset(group)
    for i, ch in enumerate(("batt_v_a", "bus_v", "rw2_temp")):
        assert d.observe(l1_out(60.0 * (10 + i), ch)) == []  # spread over three different steps
    for ch in ("batt_v_a", "bus_v", "rw2_temp"):
        assert d.observe(l1_out(9000.0, ch, sub="cusum")) == []  # a slow statistic is not a glitch
