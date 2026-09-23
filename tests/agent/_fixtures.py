"""A minimal but realistic `Case` for agent tests, not itself a test module."""

from __future__ import annotations

from sentinel_agent.case import (
    Case,
    ChannelBaseline,
    ChannelWindow,
    Contribution,
    DetectorOutput,
    EvidenceBundle,
    IncidentSummary,
    PacketIntegritySummary,
    RelatedIncident,
    SpaceWeatherEvent,
    TelemetryPoint,
)


def make_case(*, explanation: str = "auth failure burst from unknown source") -> Case:
    summary = IncidentSummary(
        id="inc-1",
        session_id="sess-1",
        opened_at="2024-05-01T00:00:00+00:00",
        closed_at="2024-05-01T00:05:00+00:00",
        status="closed",
        verdict="cyberattack",
        confidence=0.87,
        severity="high",
        posterior={"cyberattack": 0.87, "nominal": 0.04, "needs_human": 0.09},
        affected_channels=["auth_link"],
        model_version="dcb0100109c4",
        synthetic=True,
    )
    evidence = EvidenceBundle(
        features={"auth_anomaly": 0.9, "cmd_rate": 0.2},
        evidence_for=[Contribution(feature="auth_anomaly", value=0.9, contribution=1.4)],
        evidence_against=[Contribution(feature="nominal_prior", value=1.0, contribution=-0.3)],
        runner_up="sensor_malfunction",
    )
    outputs = [
        DetectorOutput(
            ts="2024-05-01T00:01:00+00:00",
            detector="l4.auth_anomaly",
            layer="l4",
            channel=None,
            score=0.95,
            fired=True,
            explanation=explanation,
            evidence=[{"source": "ground_station_7"}],
        )
    ]
    telemetry = {
        "auth_link": ChannelWindow(
            channel="auth_link",
            unit=None,
            points=[TelemetryPoint(ts="2024-05-01T00:00:00+00:00", value=1.0)],
        )
    }
    baselines = {
        "auth_link": ChannelBaseline(
            channel="auth_link", mean=0.1, std=0.05, n=500, note="session baseline"
        )
    }
    related = [
        RelatedIncident(
            id="inc-0",
            opened_at="2024-04-30T23:00:00+00:00",
            verdict="needs_human",
            confidence=0.4,
            severity="medium",
            affected_channels=["auth_link"],
        )
    ]
    space_weather = [
        SpaceWeatherEvent(
            kind="FLR", event_time="2024-05-01T00:02:00+00:00", end_time=None, magnitude=None
        )
    ]
    packet_integrity = PacketIntegritySummary(
        window_start="2024-04-30T23:30:00+00:00",
        window_end="2024-05-01T00:35:00+00:00",
        packets=120,
        auth_failures=14,
        malformed_frames=0,
        commands=6,
        commands_out_of_contact=2,
        auth_events=14,
        auth_failures_side=14,
    )
    return Case(
        summary=summary,
        evidence=evidence,
        detector_outputs=outputs,
        telemetry=telemetry,
        baselines=baselines,
        related=related,
        space_weather=space_weather,
        packet_integrity=packet_integrity,
    )
