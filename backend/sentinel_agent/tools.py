"""Read-only investigation tools. Each tool is a pure function ``(Case, **args) -> dict`` — no
DB, no network, no mutation — so the read-only guarantee is enforced by the signature, not by
asking the model nicely.

Tool *results* are returned to the model as plain JSON in a ``tool_result`` block. Nothing in a
result is ever executed, templated into a prompt, or reinterpreted as an instruction: some of the
fields flowing through here (opcode names, auth sources, detector explanations) are
attacker-controllable free text in the simulator, precisely because this platform investigates
possible attacks. ``client.py``'s system prompt tells the model to treat it as data; this module's
job is narrower and stronger — it guarantees calling a tool can never do anything but read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from .case import Case


def get_incident_summary(case: Case, **_: Any) -> dict[str, Any]:
    return asdict(case.summary)


def get_evidence(case: Case, **_: Any) -> dict[str, Any]:
    return {
        "features": case.evidence.features,
        "evidence_for": [asdict(c) for c in case.evidence.evidence_for],
        "evidence_against": [asdict(c) for c in case.evidence.evidence_against],
        "runner_up": case.evidence.runner_up,
    }


def get_detector_outputs(
    case: Case, layer: str | None = None, detector: str | None = None, **_: Any
) -> dict[str, Any]:
    outs = case.detector_outputs
    if layer:
        outs = [o for o in outs if o.layer == layer]
    if detector:
        outs = [o for o in outs if o.detector == detector]
    return {"count": len(outs), "outputs": [asdict(o) for o in outs[:200]]}


def get_telemetry_window(case: Case, channel: str, **_: Any) -> dict[str, Any]:
    w = case.telemetry.get(channel)
    if w is None:
        return {
            "error": f"no telemetry cached for channel {channel!r}",
            "available": sorted(case.telemetry),
        }
    return {"channel": w.channel, "unit": w.unit, "points": [asdict(p) for p in w.points]}


def get_channel_baseline(case: Case, channel: str, **_: Any) -> dict[str, Any]:
    b = case.baselines.get(channel)
    if b is None:
        return {
            "error": f"no baseline for channel {channel!r}",
            "available": sorted(case.baselines),
        }
    return asdict(b)


def get_related_incidents(case: Case, **_: Any) -> dict[str, Any]:
    return {"count": len(case.related), "incidents": [asdict(r) for r in case.related]}


def get_space_weather_context(case: Case, **_: Any) -> dict[str, Any]:
    if not case.space_weather:
        return {"events": [], "note": "no cached DONKI event overlaps this window"}
    return {"events": [asdict(e) for e in case.space_weather]}


def get_packet_integrity(case: Case, **_: Any) -> dict[str, Any]:
    return asdict(case.packet_integrity)


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_incident_summary": get_incident_summary,
    "get_evidence": get_evidence,
    "get_detector_outputs": get_detector_outputs,
    "get_telemetry_window": get_telemetry_window,
    "get_channel_baseline": get_channel_baseline,
    "get_related_incidents": get_related_incidents,
    "get_space_weather_context": get_space_weather_context,
    "get_packet_integrity": get_packet_integrity,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_incident_summary",
        "description": (
            "The incident's verdict, confidence, severity, posterior over all classes, "
            "and affected channels."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_evidence",
        "description": (
            "The exact attribution features and their signed contributions for and against "
            "the chosen verdict."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_detector_outputs",
        "description": (
            "Raw per-detector scores and evidence that fired during the incident window, "
            "optionally filtered by layer or detector name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["l1", "l2", "l3", "l4", "l5"]},
                "detector": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_telemetry_window",
        "description": (
            "Raw values for one affected channel across the incident window plus a margin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}},
            "required": ["channel"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_channel_baseline",
        "description": (
            "The mean and standard deviation of one channel outside the incident window, for "
            "comparison. This is a session baseline, not a calibrated nominal range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"channel": {"type": "string"}},
            "required": ["channel"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_related_incidents",
        "description": (
            "Other incidents in the same session, for correlating a pattern across channels or "
            "time."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_space_weather_context",
        "description": (
            "Cached NASA DONKI space-weather events (solar flares, SEPs, geomagnetic storms) "
            "overlapping the incident window."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_packet_integrity",
        "description": (
            "Counts of authentication failures, malformed frames, and out-of-contact commands "
            "during the window — evidence for or against tampering."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
