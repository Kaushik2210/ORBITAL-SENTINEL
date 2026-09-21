"""Regenerate ``data/scenarios/*.yaml`` from the compact table below.

The YAML files are the source of truth (readable, diffable, validated at load time by
``sentinel_sim.scenarios.spec``); this script only exists so the whole library stays consistent.
Usage: uv run python scripts/gen_scenarios.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OUT = Path("data/scenarios")
EPS, ADCS = 0x200, 0x201  # APIDs of the synthetic bus
A, B = 800, 1400  # default fault window (steps)

Scn = dict[str, Any]


def fx(type_: str, start: int = A, end: int = B, **params: Any) -> dict[str, Any]:
    return {"type": type_, "start": start, "end": end, "params": params}


def scn(
    id_: str, title: str, narrative: str, klass: str, subtype: str, seed: int,
    effects: list[dict[str, Any]] | None = None, **extra: Any,
) -> Scn:  # fmt: skip
    return {
        "id": id_, "title": title, "narrative": narrative, "class": klass, "subtype": subtype,
        "seed": seed, **extra, "effects": effects or [],
    }  # fmt: skip


ATK, MAL, MECH, ENV, NOM = (
    "cyberattack",
    "sensor_malfunction",
    "mechanical_failure",
    "environmental",
    "nominal",
)

SCENARIOS: list[Scn] = [
    # ------------------------------------------------------------------ nominal
    scn(
        "nominal_a",
        "A quiet day",
        "Nothing is wrong. Used to measure false alarms.",
        NOM,
        "nominal",
        1001,
    ),
    scn(
        "nominal_b", "Another quiet day", "Nothing is wrong (different seed).", NOM, "nominal", 1002
    ),
    scn(
        "nominal_c", "A third quiet day", "Nothing is wrong (different seed).", NOM, "nominal", 1003
    ),
    # ------------------------------------------------------------------ telemetry manipulation
    scn(
        "manip_ramp_battv",
        "The slow bias",
        "A man-in-the-middle with the link key slowly raises one battery-voltage reading; every frame is validly signed.",
        ATK,
        "telemetry_manipulation",
        2001,
        [fx("rewrite", 700, 1500, apid=EPS, channel_index=0, magnitude=1.5, ramp=True)],
        attacker={"has_key": True, "position": "link"},
        sparta=["EX-0014/02"],
        attack=["T1565", "T1557"],
        confusable_with=["sensor_drift_battv", "battery_degradation"],
        hard=True,
    ),
    scn(
        "manip_bias_load",
        "Load current scaled",
        "The load-current sensor is scaled by 40%, breaking the power balance while staying plausible.",
        ATK,
        "telemetry_manipulation",
        2002,
        [fx("scale", A, B, targets=["load_i"], gain=0.4)],
        attacker={"position": "sensor"},
        sparta=["EX-0014/03"],
        attack=["T1565"],
    ),
    scn(
        "manip_lowslow_battv",
        "Low and slow",
        "A tiny drift, well inside limits, engineered not to trip single-channel checks.",
        ATK,
        "low_and_slow_drift",
        2003,
        [fx("rewrite", 400, 1700, apid=EPS, channel_index=0, magnitude=0.45, ramp=True)],
        attacker={"has_key": True, "position": "link"},
        sparta=["EX-0014/02"],
        attack=["T1565", "T1557"],
        confusable_with=["sensor_drift_battv"],
        hard=True,
    ),
    scn(
        "manip_freeze_intrusion",
        "Frozen after a strange login",
        "A wheel vibration reading is frozen right after a login from an unknown account.",
        ATK,
        "telemetry_freeze",
        2004,
        [
            fx("auth_abuse", 780, 781, variant="new_source"),
            fx("freeze", A, B, targets=["rw1_vib_a"]),
        ],
        attacker={"position": "ground"},
        sparta=["EX-0014/03", "IA-0007/02"],
        attack=["T1078", "T1565"],
        confusable_with=["sensor_stuck_vib"],
        hard=True,
    ),
    # ------------------------------------------------------------------ command injection
    scn(
        "cmd_unauthorized",
        "Unknown opcode",
        "A command whose opcode is not on the whitelist arrives from a legitimate station.",
        ATK,
        "command_injection",
        2101,
        [fx("command_injection", variant="unauthorized_opcode", prob=0.5)],
        attacker={"position": "ground"},
        sparta=["IA-0007/02", "EX-0013/02"],
        attack=["T1078"],
    ),
    scn(
        "cmd_out_of_window",
        "Command between passes",
        "A valid opcode is sent while no ground station is in contact.",
        ATK,
        "command_injection",
        2102,
        [fx("command_injection", variant="out_of_window", prob=0.5)],
        attacker={"position": "ground"},
        sparta=["IA-0007/02"],
        attack=["T1078"],
    ),
    scn(
        "cmd_unknown_source",
        "Command from nowhere",
        "A valid opcode arrives from a station that is not registered.",
        ATK,
        "command_injection",
        2103,
        [fx("command_injection", variant="unknown_source", prob=0.5)],
        attacker={"position": "ground"},
        sparta=["IA-0007/02"],
        attack=["T1078"],
    ),
    scn(
        "cmd_unauthenticated",
        "Command without authentication",
        "A command fails its authentication check.",
        ATK,
        "command_injection",
        2104,
        [fx("command_injection", variant="unauthenticated", prob=0.5)],
        attacker={"position": "ground"},
        sparta=["IA-0007/02"],
        attack=["T1078"],
    ),
    scn(
        "cmd_whitelisted_anomalous",
        "Legitimate opcode, wrong behavior",
        "A whitelisted, authenticated opcode is sent in a burst.",
        ATK,
        "command_injection",
        2105,
        [fx("command_injection", A, A + 80, variant="whitelisted_anomalous", prob=0.7)],
        attacker={"position": "ground"},
        sparta=["EX-0013/01", "IA-0007/02"],
        attack=["T1078"],
        hard=True,
    ),
    # ------------------------------------------------------------------ replay
    scn(
        "replay_append",
        "Old frames, again",
        "Recorded valid frames are re-sent alongside live traffic.",
        ATK,
        "replay",
        2201,
        [fx("replay", 900, 1100, source_start=300, mode="append")],
        attacker={"position": "link"},
        sparta=["EX-0001/02"],
        attack=["T1557"],
        confusable_with=["sensor_stuck_battv"],
        hard=True,
    ),
    scn(
        "replay_replace",
        "Only old frames",
        "The live feed is suppressed and only recorded frames reach the ground.",
        ATK,
        "replay",
        2202,
        [fx("replay", 900, 1100, source_start=300, mode="replace")],
        attacker={"position": "link"},
        sparta=["EX-0001/02"],
        attack=["T1557"],
        confusable_with=["sensor_stuck_battv"],
        hard=True,
    ),
    # ------------------------------------------------------------------ abnormal authentication
    scn(
        "auth_bruteforce",
        "Password guessing",
        "A burst of failed logins from an unknown address.",
        ATK,
        "abnormal_authentication",
        2301,
        [fx("auth_abuse", A, A + 20, variant="bruteforce", attempts=4)],
        attacker={"position": "ground"},
        attack=["T1110"],
        hard=True,
    ),
    scn(
        "auth_offwindow",
        "Login at the wrong time",
        "A known operator logs in outside any pass.",
        ATK,
        "abnormal_authentication",
        2302,
        [fx("auth_abuse", 860, 861, variant="offwindow")],
        attacker={"position": "ground"},
        attack=["T1078"],
    ),
    scn(
        "auth_new_source",
        "Login from a new account",
        "A successful login from an account that is not on the roster.",
        ATK,
        "abnormal_authentication",
        2303,
        [fx("auth_abuse", 830, 831, variant="new_source")],
        attacker={"position": "ground"},
        attack=["T1078"],
    ),
    # ------------------------------------------------------------------ communication anomalies
    scn(
        "comms_jamming",
        "The link is jammed",
        "SNR collapses, bit errors and loss rise, and about a third of frames vanish.",
        ATK,
        "jamming",
        2401,
        [fx("jamming", snr_drop_db=11.0, ber_decades=4.0, loss_pct=10.0, frame_loss=0.35)],
        attacker={"position": "link"},
        sparta=["EX-0016/02"],
        attack=["T1498"],
        confusable_with=["dos_dropout"],
        hard=True,
    ),
    scn(
        "comms_rogue_uplink",
        "A rogue uplink",
        "Commands arrive from an unregistered transmitter and the link quality dips.",
        ATK,
        "rogue_uplink",
        2402,
        [
            fx("command_injection", variant="unknown_source", prob=0.5),
            fx("jamming", snr_drop_db=4.5, ber_decades=1.5, loss_pct=1.0),
        ],
        attacker={"position": "ground"},
        sparta=["EX-0016/01", "IA-0007/02"],
        attack=["T1078"],
    ),
    # ------------------------------------------------------------------ sensor spoofing
    scn(
        "spoof_battv_clean",
        "The too-clean sensor",
        "One battery-voltage sensor reports the true value plus a growing offset, but without any sensor noise.",
        ATK,
        "sensor_spoofing",
        2501,
        [fx("spoof_clean", 700, 1500, targets=["batt_v_a"], magnitude=0.5, truth_key="batt_v")],
        attacker={"position": "sensor"},
        sparta=["EX-0014/03"],
        attack=["T1565"],
        confusable_with=["sensor_drift_battv"],
        hard=True,
    ),
    scn(
        "spoof_vib_hide",
        "Hiding the wear",
        "One vibration sensor is made to keep reporting a healthy level while the wheel really wears.",
        ATK,
        "sensor_spoofing",
        2502,
        [fx("spoof_baseline", 700, 1500, targets=["rw1_vib_a"], level=0.078, noise=0.03)],
        aging={"wheel": [700, 1500, 0.05, 0.85]},
        attacker={"position": "sensor"},
        sparta=["EX-0014/03"],
        attack=["T1565"],
        confusable_with=["bearing_wear"],
        hard=True,
    ),
    # ------------------------------------------------------------------ denial of service
    scn(
        "dos_flood",
        "Frame flood",
        "Each frame is repeated twenty times.",
        ATK,
        "denial_of_service",
        2601,
        [fx("flood", A, A + 200, copies=20)],
        attacker={"position": "link"},
        sparta=["EX-0013/01"],
        attack=["T1498", "T1499"],
    ),
    scn(
        "dos_malformed",
        "Garbage frames",
        "Malformed frames are injected among the real ones.",
        ATK,
        "denial_of_service",
        2602,
        [fx("flood", A, A + 200, copies=1, malformed=15)],
        attacker={"position": "link"},
        sparta=["EX-0013/02"],
        attack=["T1499"],
    ),
    scn(
        "dos_dropout",
        "Traffic disappears",
        "Frames from one subsystem are dropped at the link.",
        ATK,
        "denial_of_service",
        2603,
        [fx("frame_drop", A, A + 300, prob=0.9)],
        attacker={"position": "link"},
        sparta=["EX-0013/01"],
        attack=["T1498"],
        confusable_with=["comms_jamming"],
        hard=True,
    ),
    # ------------------------------------------------------------------ sensor malfunctions
    scn(
        "sensor_stuck_battv",
        "A stuck sensor",
        "A battery-voltage sensor freezes at one value; frames and counters are perfectly normal.",
        MAL,
        "sensor_stuck",
        3001,
        [fx("freeze", targets=["batt_v_a"])],
        confusable_with=["replay_replace", "replay_append"],
        hard=True,
    ),
    scn(
        "sensor_stuck_vib",
        "A stuck vibration sensor",
        "A wheel accelerometer freezes.",
        MAL,
        "sensor_stuck",
        3002,
        [fx("freeze", targets=["rw1_vib_a"])],
        confusable_with=["manip_freeze_intrusion"],
        hard=True,
    ),
    scn(
        "sensor_noise_vib",
        "A noisy sensor",
        "A wheel accelerometer gets ten times noisier.",
        MAL,
        "sensor_noise",
        3003,
        [fx("noise", targets=["rw1_vib_b"], sigma=0.03)],
    ),
    scn(
        "sensor_dropout_battt",
        "A sensor drops out",
        "The battery temperature sensor intermittently reports nothing.",
        MAL,
        "sensor_dropout",
        3004,
        [fx("dropout", targets=["batt_t"], prob=0.6)],
    ),
    scn(
        "sensor_drift_battv",
        "A drifting sensor",
        "One battery-voltage sensor drifts and gets noisier as it ages; the other is fine.",
        MAL,
        "sensor_drift",
        3005,
        [fx("drift", 700, 1500, targets=["batt_v_a"], magnitude=0.5, noise_growth=0.06)],
        confusable_with=["spoof_battv_clean", "manip_lowslow_battv", "battery_degradation"],
        hard=True,
    ),
    # ------------------------------------------------------------------ mechanical / component
    scn(
        "battery_degradation",
        "The battery is aging",
        "Capacity fades and resistance rises along a real cell's aging trajectory; both voltage sensors agree.",
        MECH,
        "battery_degradation",
        4001,
        aging={"battery": [500, 1700, 0.05, 1.0]},
        confusable_with=["sensor_drift_battv", "manip_ramp_battv"],
        hard=True,
    ),
    scn(
        "bearing_wear",
        "A bearing is wearing out",
        "Wheel vibration rises along the real IMS run-to-failure trajectory; both accelerometers and the temperature agree.",
        MECH,
        "bearing_wear",
        4002,
        aging={"wheel": [400, 1700, 0.05, 1.0]},
        confusable_with=["spoof_vib_hide", "sensor_noise_vib"],
        hard=True,
    ),
    # ------------------------------------------------------------------ environmental
    scn(
        "seu_sep_aligned",
        "A solar particle event",
        "Bit flips hit several unrelated channels at once during a real solar energetic particle event.",
        ENV,
        "single_event_upset",
        5001,
        [
            fx(
                "seu",
                890,
                921,
                targets=["batt_v_a", "bus_v", "rw1_speed", "rw2_temp", "solar_i"],
                steps=[900, 905, 912],
            )
        ],
        weather={
            "kind": "SEP",
            "date_from": "2024-05-01",
            "date_to": "2024-05-30",
            "align_step": 895,
        },
        sparta=["EX-0007"],
        confusable_with=["seu_lookalike_attack"],
        hard=True,
    ),
    scn(
        "seu_lookalike_attack",
        "Glitches that look like radiation",
        "An attacker with the link key injects abrupt jumps on several channels at once, at a time with no space weather.",
        ATK,
        "glitch_injection",
        2701,
        [fx("bias", s, s + 1, targets=["batt_v_a"], magnitude=6.0) for s in (900, 905, 912)]
        + [fx("bias", s, s + 1, targets=["bus_v"], magnitude=-4.0) for s in (900, 905, 912)]
        + [fx("bias", s, s + 1, targets=["rw2_temp"], magnitude=25.0) for s in (900, 905, 912)],
        attacker={"has_key": True, "position": "sensor"},
        sparta=["EX-0014/03"],
        attack=["T1565"],
        confusable_with=["seu_sep_aligned"],
        hard=True,
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.yaml"):
        old.unlink()
    for s in SCENARIOS:
        (OUT / f"{s['id']}.yaml").write_text(
            yaml.safe_dump(s, sort_keys=False, width=100), encoding="utf-8"
        )
    print(f"wrote {len(SCENARIOS)} scenarios to {OUT}")


if __name__ == "__main__":
    main()
