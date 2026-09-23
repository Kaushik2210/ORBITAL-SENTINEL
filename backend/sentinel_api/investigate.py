"""Assembles a ``sentinel_agent.case.Case`` bundle from the database for one incident, and
renders the agent's report to Markdown.

Lives in the API layer, not in ``sentinel_agent``, because it needs DB access; the dependency
runs api -> agent, never the other way (ADR 0002), so the agent package stays a pure function of
whatever data it is handed and is trivial to unit-test offline.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_agent.case import (
    Case,
    ChannelBaseline,
    ChannelWindow,
    Contribution,
    EvidenceBundle,
    IncidentSummary,
    PacketIntegritySummary,
    RelatedIncident,
    SpaceWeatherEvent,
)
from sentinel_agent.case import DetectorOutput as AgentDetectorOutput
from sentinel_agent.case import TelemetryPoint as AgentTelemetryPoint
from sentinel_agent.report import Report

from .db.models import (
    AuthEventRow,
    Channel,
    CommandLog,
    DetectorOutputRow,
    DonkiEventRow,
    Incident,
    MalformedFrame,
    PacketRow,
    Telemetry,
)

MARGIN = timedelta(minutes=30)
SPACE_WEATHER_MARGIN = timedelta(hours=6)
TELEMETRY_LIMIT = 1000
BASELINE_LIMIT = 3000


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def build_case(factory: async_sessionmaker[AsyncSession], incident_id: str) -> Case | None:
    """Fetch everything the agent's tools can answer from, once, up front. ``None`` if unknown."""
    async with factory() as db:
        row = await db.get(Incident, incident_id)
        if row is None:
            return None
        opened = _utc(row.opened_at)
        closed = _utc(row.closed_at) if row.closed_at else opened

        d = row.top_evidence or {}
        evidence = EvidenceBundle(
            features=dict(row.features),
            evidence_for=[Contribution(**c) for c in d.get("for", [])],
            evidence_against=[Contribution(**c) for c in d.get("against", [])],
            runner_up=d.get("runner_up"),
        )
        summary = IncidentSummary(
            id=row.id,
            session_id=row.session_id,
            opened_at=opened.isoformat(),
            closed_at=closed.isoformat() if row.closed_at else None,
            status=row.status,
            verdict=row.verdict,
            confidence=row.confidence,
            severity=row.severity,
            posterior=dict(row.posterior),
            affected_channels=list(row.affected_channels),
            model_version=row.model_version,
            synthetic=row.synthetic,
        )

        outs = (
            (
                await db.execute(
                    select(DetectorOutputRow)
                    .where(
                        DetectorOutputRow.session_id == row.session_id,
                        DetectorOutputRow.ts >= opened,
                        DetectorOutputRow.ts <= closed,
                    )
                    .order_by(DetectorOutputRow.ts, DetectorOutputRow.n)
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        detector_outputs = [
            AgentDetectorOutput(
                ts=_utc(o.ts).isoformat(),
                detector=o.detector,
                layer=o.layer,
                channel=o.channel_id,
                score=o.score,
                fired=o.fired,
                explanation=o.explanation,
                evidence=list(o.evidence),
            )
            for o in outs
        ]

        channels = list(row.affected_channels) or sorted(
            {o.channel_id for o in outs if o.channel_id}
        )
        win_start, win_end = opened - MARGIN, closed + MARGIN
        telemetry: dict[str, ChannelWindow] = {}
        baselines: dict[str, ChannelBaseline] = {}
        for ch in channels:
            chan_row = await db.get(Channel, ch)
            unit = chan_row.unit if chan_row else None
            pts = (
                (
                    await db.execute(
                        select(Telemetry)
                        .where(
                            Telemetry.session_id == row.session_id,
                            Telemetry.channel_id == ch,
                            Telemetry.ts >= win_start,
                            Telemetry.ts <= win_end,
                        )
                        .order_by(Telemetry.ts)
                        .limit(TELEMETRY_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            telemetry[ch] = ChannelWindow(
                channel=ch,
                unit=unit,
                points=[AgentTelemetryPoint(ts=_utc(p.ts).isoformat(), value=p.value) for p in pts],
            )
            outside = (
                (
                    await db.execute(
                        select(Telemetry.value)
                        .where(
                            Telemetry.session_id == row.session_id,
                            Telemetry.channel_id == ch,
                            (Telemetry.ts < win_start) | (Telemetry.ts > win_end),
                        )
                        .limit(BASELINE_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            if len(outside) >= 2:
                baselines[ch] = ChannelBaseline(
                    channel=ch,
                    mean=statistics.fmean(outside),
                    std=statistics.pstdev(outside),
                    n=len(outside),
                    note="mean/std of this channel outside the incident window in this session",
                )

        related_rows = (
            (
                await db.execute(
                    select(Incident)
                    .where(Incident.session_id == row.session_id, Incident.id != row.id)
                    .order_by(Incident.opened_at)
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        related = [
            RelatedIncident(
                id=r.id,
                opened_at=_utc(r.opened_at).isoformat(),
                verdict=r.verdict,
                confidence=r.confidence,
                severity=r.severity,
                affected_channels=list(r.affected_channels),
            )
            for r in related_rows
        ]

        sw_rows = (
            (
                await db.execute(
                    select(DonkiEventRow)
                    .where(
                        DonkiEventRow.event_time <= opened + SPACE_WEATHER_MARGIN,
                        (DonkiEventRow.end_time.is_(None))
                        | (DonkiEventRow.end_time >= opened - SPACE_WEATHER_MARGIN),
                    )
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        space_weather = [
            SpaceWeatherEvent(
                kind=e.kind,
                event_time=_utc(e.event_time).isoformat(),
                end_time=_utc(e.end_time).isoformat() if e.end_time else None,
                magnitude=e.magnitude,
            )
            for e in sw_rows
        ]

        packets = (
            (
                await db.execute(
                    select(PacketRow).where(
                        PacketRow.session_id == row.session_id,
                        PacketRow.ts >= win_start,
                        PacketRow.ts <= win_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        malformed = (
            (
                await db.execute(
                    select(MalformedFrame).where(
                        MalformedFrame.session_id == row.session_id,
                        MalformedFrame.ts >= win_start,
                        MalformedFrame.ts <= win_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        commands = (
            (
                await db.execute(
                    select(CommandLog).where(
                        CommandLog.session_id == row.session_id,
                        CommandLog.ts >= win_start,
                        CommandLog.ts <= win_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        auth_events = (
            (
                await db.execute(
                    select(AuthEventRow).where(
                        AuthEventRow.session_id == row.session_id,
                        AuthEventRow.ts >= win_start,
                        AuthEventRow.ts <= win_end,
                    )
                )
            )
            .scalars()
            .all()
        )
        packet_integrity = PacketIntegritySummary(
            window_start=win_start.isoformat(),
            window_end=win_end.isoformat(),
            packets=len(packets),
            auth_failures=sum(1 for p in packets if not p.auth_ok),
            malformed_frames=len(malformed),
            commands=len(commands),
            commands_out_of_contact=sum(1 for c in commands if not c.in_contact_window),
            auth_events=len(auth_events),
            auth_failures_side=sum(1 for a in auth_events if not a.success),
        )

        return Case(
            summary=summary,
            evidence=evidence,
            detector_outputs=detector_outputs,
            telemetry=telemetry,
            baselines=baselines,
            related=related,
            space_weather=space_weather,
            packet_integrity=packet_integrity,
        )


def report_markdown(report: Report) -> str:
    lines = [
        f"# Investigation report: {report.verdict.value}",
        "",
        f"**Confidence:** {report.confidence:.2f}",
        "",
        report.summary,
        "",
        "## Key evidence",
        *[f"- {e}" for e in report.key_evidence],
        "",
        "## Reasoning",
        *([f"- {r}" for r in report.reasoning] or ["- (none recorded)"]),
        "",
        "## Recommended action",
        report.recommended_action,
        "",
        "## Caveats",
        *([f"- {c}" for c in report.caveats] or ["- none"]),
    ]
    return "\n".join(lines)
