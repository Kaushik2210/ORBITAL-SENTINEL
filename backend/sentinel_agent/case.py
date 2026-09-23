"""The read-only data bundle the investigation agent's tools operate over.

Built once per investigation by the caller (``sentinel_api.investigate``, which has DB access)
and handed to the agent as a `Case`. Every field is already fetched and frozen before the model
sees the first token, so a tool call can never trigger a new query, a write, or a network call —
the read-only guarantee is mechanical, not a matter of prompting discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Contribution:
    feature: str
    value: float
    contribution: float


@dataclass(frozen=True, slots=True)
class DetectorOutput:
    ts: str
    detector: str
    layer: str
    channel: str | None
    score: float
    fired: bool
    explanation: str
    evidence: list[Any]


@dataclass(frozen=True, slots=True)
class TelemetryPoint:
    ts: str
    value: float


@dataclass(frozen=True, slots=True)
class ChannelWindow:
    channel: str
    unit: str | None
    points: list[TelemetryPoint]


@dataclass(frozen=True, slots=True)
class ChannelBaseline:
    channel: str
    mean: float
    std: float
    n: int
    note: str


@dataclass(frozen=True, slots=True)
class RelatedIncident:
    id: str
    opened_at: str
    verdict: str
    confidence: float
    severity: str
    affected_channels: list[str]


@dataclass(frozen=True, slots=True)
class SpaceWeatherEvent:
    kind: str
    event_time: str
    end_time: str | None
    magnitude: float | None


@dataclass(frozen=True, slots=True)
class PacketIntegritySummary:
    window_start: str
    window_end: str
    packets: int
    auth_failures: int
    malformed_frames: int
    commands: int
    commands_out_of_contact: int
    auth_events: int
    auth_failures_side: int  # from the auth_events side-channel, distinct from packet auth_ok


@dataclass(frozen=True, slots=True)
class IncidentSummary:
    id: str
    session_id: str
    opened_at: str
    closed_at: str | None
    status: str
    verdict: str
    confidence: float
    severity: str
    posterior: dict[str, float]
    affected_channels: list[str]
    model_version: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    features: dict[str, float]
    evidence_for: list[Contribution]
    evidence_against: list[Contribution]
    runner_up: str | None


@dataclass(frozen=True, slots=True)
class Case:
    """Everything the agent's tools can answer questions from, fetched once up front."""

    summary: IncidentSummary
    evidence: EvidenceBundle
    detector_outputs: list[DetectorOutput]
    telemetry: dict[str, ChannelWindow]  # keyed by channel id, incident window +/- a margin
    baselines: dict[str, ChannelBaseline]
    related: list[RelatedIncident]
    space_weather: list[SpaceWeatherEvent]
    packet_integrity: PacketIntegritySummary
