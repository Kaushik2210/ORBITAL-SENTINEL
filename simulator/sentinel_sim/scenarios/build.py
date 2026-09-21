"""Turn a validated :class:`ScenarioSpec` into a mission, its ground truth and weather context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sentinel_core.events import WeatherEvent
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import pcoe
from sentinel_sim.donki import DonkiClient, flare_flux, parse_time, to_weather_events
from sentinel_sim.mission import DEFAULT_FRAME_KEY, Injector, Mission, MissionConfig

from .effects import REGISTRY, AgingPlan, Window
from .spec import QUIET_START, GroundTruth, ScenarioSpec, truth_from

DONKI_KINDS = ("FLR", "SEP", "GST")
DEFAULT_CACHE = Path("data/cache/donki")


@dataclass(slots=True)
class BuiltScenario:
    spec: ScenarioSpec
    mission: Mission
    truth: GroundTruth
    weather: list[WeatherEvent] = field(default_factory=list)
    start: datetime = QUIET_START


def _pick_event(spec: ScenarioSpec, client: DonkiClient) -> datetime:
    w = spec.weather
    assert w is not None
    events = client.get_events(
        w.kind, date.fromisoformat(w.date_from), date.fromisoformat(w.date_to), allow_network=False
    )
    if w.kind == "FLR":
        cands = [
            (flare_flux(str(e.get("classType", ""))) or 0.0, e)
            for e in events
            if e.get("peakTime")
            and (flare_flux(str(e.get("classType", ""))) or 0.0) >= (w.min_flux or 0)
        ]
        if not cands:
            raise LookupError(f"{spec.id}: no flare above the requested flux in the cached window")
        return parse_time(max(cands, key=lambda c: c[0])[1]["beginTime"])
    key = "eventTime" if w.kind == "SEP" else "startTime"
    times = sorted(parse_time(e[key]) for e in events if e.get(key))
    if not times:
        raise LookupError(f"{spec.id}: no {w.kind} events in the cached window")
    return times[0]


def _weather_for(start: datetime, steps: int, client: DonkiClient) -> list[WeatherEvent] | None:
    """Weather events overlapping the session, or None if the cache does not cover it."""
    d0, d1 = start.date(), (start + timedelta(seconds=steps * STEP_SECONDS)).date()
    if not all(client.has_coverage(k, d0, d1) for k in DONKI_KINDS):
        return None
    out: list[WeatherEvent] = []
    for kind in DONKI_KINDS:
        out += to_weather_events(kind, client.get_events(kind, d0, d1, allow_network=False), start)
    return [
        w
        for w in sorted(out, key=lambda w: w.begin_ts)
        if w.end_ts >= 0 and w.begin_ts <= steps * STEP_SECONDS
    ]


def build_scenario(
    spec: ScenarioSpec,
    data_root: Path = Path("data/raw"),
    donki_cache: Path = DEFAULT_CACHE,
    frame_key: bytes = DEFAULT_FRAME_KEY,
    battery: pcoe.BatteryTrajectory | None = None,
    wheel: pcoe.WheelTrajectory | None = None,
) -> BuiltScenario:
    client = DonkiClient(donki_cache)
    start = spec.start or QUIET_START
    weather_context = "quiet-verified"
    if spec.weather is not None:
        try:
            start = _pick_event(spec, client) - timedelta(
                seconds=spec.weather.align_step * STEP_SECONDS
            )
            weather_context = "aligned"
        except (LookupError, FileNotFoundError):
            weather_context = "unavailable"
    weather = _weather_for(start, spec.steps, client)
    if weather is None:
        weather, weather_context = [], "unavailable"

    battery = battery or pcoe.load_battery(data_root)
    wheel = wheel or pcoe.load_wheel(data_root)
    injectors: list[Injector] = [
        REGISTRY[e.type](
            Window(e.start, e.end), e.params, spec.seed, f"effect:{i}:{e.type}", frame_key
        )
        for i, e in enumerate(spec.effects)
    ]
    aging = AgingPlan(battery=spec.aging.battery, wheel=spec.aging.wheel)
    mission = Mission(
        MissionConfig(
            seed=spec.seed, start=start.astimezone(UTC), frame_key=frame_key, data_root=data_root
        ),
        injectors=tuple(injectors),
        aging=aging,
        battery=battery,
        wheel=wheel,
    )
    sources = {"battery": battery.source, "wheel": wheel.source}
    return BuiltScenario(spec, mission, truth_from(spec, sources, weather_context), weather, start)
