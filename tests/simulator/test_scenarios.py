from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from sentinel_core.events import (
    AuthEvent,
    MalformedFrameEvent,
    PacketEvent,
    TelemetryEvent,
)
from sentinel_core.ingest import Ingestor
from sentinel_core.taxonomy import IncidentClass
from sentinel_sim import pcoe
from sentinel_sim.mission import DEFAULT_FRAME_KEY, Mission, MissionConfig
from sentinel_sim.scenarios import tags
from sentinel_sim.scenarios.build import build_scenario
from sentinel_sim.scenarios.effects import REGISTRY, Window, flip_float32_bit
from sentinel_sim.scenarios.spec import ScenarioSpec, load_all

SCN_DIR = Path("data/scenarios")
BATT, WHEEL = pcoe.parametric_battery(), pcoe.parametric_wheel()


def spec_dict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "t",
        "title": "t",
        "narrative": "t",
        "class": "sensor_malfunction",
        "subtype": "x",
        "seed": 1,
        "effects": [
            {
                "type": "bias",
                "start": 200,
                "end": 300,
                "params": {"targets": ["batt_v_a"], "magnitude": 1.0},
            }
        ],
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ library and schema


def test_library_loads_and_covers_every_required_family() -> None:
    specs = load_all(SCN_DIR)
    subtypes = {s.subtype for s in specs}
    # the 7 attack families of the brief
    for family in (
        "telemetry_manipulation", "command_injection", "replay", "abnormal_authentication",
        "jamming", "sensor_spoofing", "denial_of_service",
    ):  # fmt: skip
        assert family in subtypes, family
    # the 3 non-attack fault families (plus the mechanical and environmental ones)
    for family in (
        *("sensor_stuck", "sensor_noise", "sensor_dropout", "sensor_drift"),
        *("battery_degradation", "bearing_wear", "single_event_upset"),
    ):
        assert family in subtypes, family
    assert {s.klass for s in specs} == set(IncidentClass)


def test_every_hard_scenario_names_a_confusable_partner_or_is_a_known_ambiguity() -> None:
    specs = {s.id: s for s in load_all(SCN_DIR)}
    for s in specs.values():
        if s.hard and s.confusable_with:
            assert all(c in specs for c in s.confusable_with)
    pairs = [(s.id, c) for s in specs.values() for c in s.confusable_with]
    assert len(pairs) >= 10


def test_every_tag_used_is_in_the_verified_registry() -> None:
    for s in load_all(SCN_DIR):
        tags.validate(s.sparta, s.attack)


def test_ground_truth_is_derived_from_the_effects() -> None:
    spec = next(s for s in load_all(SCN_DIR) if s.id == "sensor_stuck_battv")
    truth = build_scenario(spec, battery=BATT, wheel=WHEEL).truth
    assert (truth.start_step, truth.end_step) == (800, 1400)
    assert truth.channels == ["batt_v_a"]
    assert truth.klass is IncidentClass.SENSOR_MALFUNCTION
    assert truth.data_sources["battery"] == "synthetic-parametric"  # labeled fallback data


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["effects"][0].update(type="teleport"), "unknown type"),
        (lambda d: d["effects"][0]["params"].pop("magnitude"), "missing params"),
        (lambda d: d["effects"][0].update(start=10), "must lie in"),
        (lambda d: d["effects"][0].update(end=99999), "must lie in"),
        (lambda d: d.update(sparta=["EX-9999"]), "unverified"),
        (lambda d: d.update(attack=["T0000"]), "unverified"),
        (lambda d: d.update(effects=[]), "needs at least one effect"),
        (lambda d: d.update(**{"class": "nominal"}), "nominal scenario cannot"),
    ],
)
def test_invalid_specs_are_rejected(mutation: Any, message: str) -> None:
    d = spec_dict()
    mutation(d)
    with pytest.raises(ValidationError, match=message):
        ScenarioSpec.model_validate(d)


def test_keyed_rewrite_requires_the_attacker_to_have_the_key() -> None:
    d = spec_dict(**{"class": "cyberattack"})
    d["effects"] = [
        {
            "type": "rewrite",
            "start": 200,
            "end": 300,
            "params": {"apid": 512, "channel_index": 0, "magnitude": 1.0},
        }
    ]
    with pytest.raises(ValidationError, match="has_key"):
        ScenarioSpec.model_validate(d)
    d["attacker"] = {"has_key": True, "position": "link"}
    ScenarioSpec.model_validate(d)


def test_yaml_round_trip_of_the_shipped_library(tmp_path: Path) -> None:
    for p in SCN_DIR.glob("*.yaml"):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        ScenarioSpec.model_validate(doc)
    assert len(list(SCN_DIR.glob("*.yaml"))) == len(load_all(SCN_DIR))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(spec_dict()), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate scenario ids"):
        load_all(tmp_path)


# ------------------------------------------------------------------ effects, on the wire


def make_mission(
    effect_type: str, params: dict[str, Any], start: int = 20, end: int = 40, seed: int = 5
) -> Mission:
    inj = REGISTRY[effect_type](
        Window(start, end), params, seed, f"effect:0:{effect_type}", DEFAULT_FRAME_KEY
    )
    return Mission(MissionConfig(seed=seed), injectors=(inj,), battery=BATT, wheel=WHEEL)


def events(m: Mission, n: int) -> list[Any]:
    ing = Ingestor(m.apids, m.config.frame_key, m.config.start)
    return [e for k in range(n) for e in ing.process_tick(m.tick(k))]


def tel(evs: list[Any], ch: str) -> list[float]:
    return [e.value for e in evs if isinstance(e, TelemetryEvent) and e.channel == ch]


def test_bias_shifts_values_only_inside_its_window_and_stays_authentic() -> None:
    clean = events(Mission(MissionConfig(seed=5), battery=BATT, wheel=WHEEL), 60)
    dirty = events(make_mission("bias", {"targets": ["load_i"], "magnitude": 2.0}), 60)
    c, d = tel(clean, "load_i"), tel(dirty, "load_i")
    assert d[10] == pytest.approx(c[10], abs=1e-4)
    assert d[30] - c[30] == pytest.approx(2.0, abs=1e-3)
    assert d[50] == pytest.approx(c[50], abs=1e-4)
    assert all(e.auth_ok for e in dirty if isinstance(e, PacketEvent))


def test_freeze_holds_one_value_and_dropout_sends_nan() -> None:
    frozen = tel(events(make_mission("freeze", {"targets": ["bus_v"]}), 60), "bus_v")
    assert len(set(frozen[20:40])) == 1
    dropped = tel(
        events(make_mission("dropout", {"targets": ["batt_t"], "prob": 1.0}), 60), "batt_t"
    )
    assert all(math.isnan(v) for v in dropped[20:40])
    assert not any(math.isnan(v) for v in dropped[:20])


def test_spoof_clean_removes_the_sensors_own_noise() -> None:
    v = tel(
        events(
            make_mission(
                "spoof_clean",
                {"targets": ["batt_v_a"], "magnitude": 0.0, "truth_key": "batt_v"},
                20,
                100,
            ),
            100,
        ),
        "batt_v_a",
    )
    noisy = np.diff(v[:20]).std()
    clean = np.diff(v[30:100]).std()
    assert (
        clean < noisy * 0.9
    )  # the true value has smooth dynamics; the real sensor adds ~20 mV noise


@given(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), st.integers(0, 31))
def test_bit_flip_twice_is_identity(x: float, bit: int) -> None:
    once = flip_float32_bit(x, bit)
    if math.isfinite(once):
        assert flip_float32_bit(once, bit) == pytest.approx(float(np.float32(x)))


def test_seu_hits_several_channels_at_once_with_finite_values() -> None:
    m = make_mission("seu", {"targets": ["batt_v_a", "bus_v", "rw2_temp"], "steps": [30]}, 20, 40)
    clean = events(Mission(MissionConfig(seed=5), battery=BATT, wheel=WHEEL), 40)
    dirty = events(m, 40)
    changed = [
        ch
        for ch in ("batt_v_a", "bus_v", "rw2_temp")
        if tel(dirty, ch)[30] != pytest.approx(tel(clean, ch)[30])
    ]
    assert changed == ["batt_v_a", "bus_v", "rw2_temp"]
    assert all(math.isfinite(tel(dirty, ch)[30]) for ch in changed)
    assert tel(dirty, "batt_v_a")[29] == pytest.approx(tel(clean, "batt_v_a")[29])


def test_replay_resends_old_frames_that_are_stale_but_validly_signed() -> None:
    m = make_mission("replay", {"source_start": 5, "mode": "append"}, 20, 30)
    evs = events(m, 40)
    stale = [e for e in evs if isinstance(e, PacketEvent) and e.ts_rx - e.ts_pkt > 600]
    assert len(stale) == 20  # 2 APIDs x 10 replayed steps
    assert all(e.auth_ok for e in stale)  # old frames still verify: replay is invisible to auth


def test_replay_replace_leaves_no_live_frames() -> None:
    evs = events(make_mission("replay", {"source_start": 5, "mode": "replace"}, 20, 30), 40)
    in_window = [e for e in evs if isinstance(e, PacketEvent) and 1200 <= e.ts_rx < 1800]
    assert in_window
    assert all(e.ts_rx - e.ts_pkt > 600 for e in in_window)


def test_keyless_tamper_breaks_authentication() -> None:
    evs = events(make_mission("tamper", {}), 40)
    bad = [e for e in evs if isinstance(e, PacketEvent) and not e.auth_ok]
    assert len(bad) == 2 * 20
    assert all(1200 <= e.ts_rx < 2400 for e in bad)


def test_keyed_rewrite_changes_values_but_frames_still_authenticate() -> None:
    clean = tel(events(Mission(MissionConfig(seed=5), battery=BATT, wheel=WHEEL), 40), "batt_v_a")
    m = make_mission("rewrite", {"apid": 0x200, "channel_index": 0, "magnitude": 1.5})
    evs = events(m, 40)
    assert tel(evs, "batt_v_a")[30] - clean[30] == pytest.approx(1.5, abs=1e-3)
    assert all(e.auth_ok for e in evs if isinstance(e, PacketEvent))


def test_flood_and_malformed_and_frame_drop() -> None:
    flood = make_mission("flood", {"copies": 10, "malformed": 3})
    ticks = [flood.tick(k) for k in range(30)]
    assert len(ticks[25].frames) == 2 * 10 + 3
    ing = Ingestor(flood.apids, flood.config.frame_key, flood.config.start)
    assert sum(isinstance(e, MalformedFrameEvent) for e in ing.process_tick(ticks[25])) >= 1
    drop = make_mission("frame_drop", {"prob": 1.0})
    counts = [len(drop.tick(k).frames) for k in range(30)]
    assert (counts[0], counts[25]) == (2, 0)


def test_jamming_degrades_the_link_only_in_its_window() -> None:
    m = make_mission("jamming", {"snr_drop_db": 10.0, "loss_pct": 9.0, "frame_loss": 1.0})
    ticks = [m.tick(k) for k in range(50)]
    assert ticks[5].links[0].snr_db > 10
    assert ticks[25].links[0].snr_db < 5
    assert ticks[25].links[0].loss_pct > 8
    assert len(ticks[25].frames) == 0
    assert ticks[45].links[0].snr_db > 10  # the window is [20, 40)


@pytest.mark.parametrize(
    ("variant", "check"),
    [
        ("unauthorized_opcode", lambda c: c.opcode == 0xEE),
        ("out_of_window", lambda c: not c.in_contact_window),
        ("unknown_source", lambda c: c.source == "GS-UNREGISTERED"),
        ("unauthenticated", lambda c: not c.auth_ok),
    ],
)
def test_command_injection_variants(variant: str, check: Any) -> None:
    m = make_mission("command_injection", {"variant": variant, "prob": 1.0})
    cmds = [c for k in range(40) for c in m.tick(k).commands if 20 <= k < 40]
    assert cmds
    assert all(check(c) for c in cmds)


def test_auth_abuse_variants() -> None:
    brute = make_mission("auth_abuse", {"variant": "bruteforce", "attempts": 3}, 20, 22)
    a: list[AuthEvent] = [x for k in range(25) for x in brute.tick(k).auths if 20 <= k < 22]
    assert len(a) == 6
    assert not any(x.success for x in a)
    new = make_mission("auth_abuse", {"variant": "new_source"}, 20, 22)
    b = [x for k in range(25) for x in new.tick(k).auths if 20 <= k < 22]
    assert len(b) == 1
    assert b[0].success


def test_scenarios_are_exactly_reproducible() -> None:
    spec = next(s for s in load_all(SCN_DIR) if s.id == "seu_lookalike_attack")

    def frames() -> list[bytes]:
        m = build_scenario(spec, battery=BATT, wheel=WHEEL).mission
        return [f.data for k in range(920) for f in m.tick(k).frames]

    assert frames() == frames()


def test_aging_plan_drives_the_bus_trajectory() -> None:
    spec = next(s for s in load_all(SCN_DIR) if s.id == "bearing_wear")
    m = build_scenario(spec, battery=BATT, wheel=WHEEL).mission
    ing = Ingestor(m.apids, m.config.frame_key, m.config.start)
    vib: list[float] = []
    for k in range(1700):
        for e in ing.process_tick(m.tick(k)):
            if isinstance(e, TelemetryEvent) and e.channel == "rw1_vib_a":
                vib.append(e.value)
    assert sum(vib[-100:]) / 100 > 2.0 * sum(vib[:100]) / 100
