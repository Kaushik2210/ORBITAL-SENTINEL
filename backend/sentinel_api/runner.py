"""Runs a session: mission -> ingestion -> database -> detection -> incidents -> live events.

A session is either a **scenario** (synthetic bus with injected faults/attacks, full engine and
attribution) or a **replay** of real SMAP/MSL channels (L1 + optional L2 + packet checks).
Attribution is *not* run on real-channel replays: the model was trained on the synthetic bus and
has not been validated on anonymized real channels, so those incidents are recorded as
``needs_human`` with an explicit note instead of an unearned class.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from sqlalchemy import insert

from sentinel_core.attribution.features import FeatureContext, extract
from sentinel_core.detection.base import DetectorOutput
from sentinel_core.detection.engine import DetectionEngine, IncidentBuilder, IncidentWindow
from sentinel_core.events import TelemetryEvent
from sentinel_core.ingest import Ingestor
from sentinel_core.taxonomy import Verdict
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim import smap_msl
from sentinel_sim.mission import Mission, MissionConfig, frame_key_from_env
from sentinel_sim.replay import ReplayEngine
from sentinel_sim.scenarios.build import build_scenario
from sentinel_sim.scenarios.spec import QUIET_START, ScenarioSpec, instantiate

from .db.models import DetectorOutputRow, Incident
from .db.models import GroundTruth as GroundTruthRow
from .db.models import Session as SessionRow
from .db.repo import create_session, register_channels
from .db.sink import DbSink
from .state import AppState, SessionRun

MAX_STORED_OUTPUTS = 300  # per incident: enough evidence to read, bounded storage
PUBLISH_EVERY = 30  # steps between repeat detection notifications for one (detector, channel)

SEVERITY = {
    Verdict.CYBERATTACK.value: "high",
    Verdict.MECHANICAL_FAILURE.value: "medium",
    Verdict.ENVIRONMENTAL.value: "medium",
    Verdict.SENSOR_MALFUNCTION.value: "low",
    Verdict.NOMINAL.value: "info",
    Verdict.NEEDS_HUMAN.value: "medium",
}


def severity_of(verdict: str, peak_score: float, span_steps: float) -> str:
    """Documented mapping: by verdict, escalated one level for a saturated, sustained incident."""
    sev = SEVERITY.get(verdict, "medium")
    if sev == "high" and peak_score >= 0.99 and span_steps >= 30:
        return "critical"
    return sev


def _finite(x: float) -> float | None:
    return x if x == x and abs(x) != float("inf") else None


async def create_run(state: AppState, body: Any) -> SessionRun:
    """Validate a request, register the session row and return its (not yet started) run."""
    if state.running() >= state.settings.max_concurrent_sessions:
        raise RuntimeError("too many concurrent sessions")
    if body.kind == "scenario":
        if body.scenario_id is None or body.scenario_id not in state.specs:
            raise LookupError("unknown scenario_id")
        spec: ScenarioSpec = instantiate(state.specs[body.scenario_id], body.variant)
        steps = min(body.steps or spec.steps, spec.steps)
        seed, start, labels = spec.seed, QUIET_START, None
        scenario_id = spec.id
    else:
        if not body.channels:
            raise ValueError("replay needs at least one channel")
        avail = set(smap_msl.available_channels(state.settings.data_root))
        unknown = [c for c in body.channels if c not in avail]
        if unknown:
            raise LookupError(f"unknown channels: {unknown}")
        lab = smap_msl.load_labels(state.settings.data_root)
        labels = {
            c: [[a - body.offset, b - body.offset] for a, b in lab[c].intervals if c in lab]
            for c in body.channels
        }
        seed, start, scenario_id = 0, QUIET_START, None
        lengths = [
            len(smap_msl.load_channel(state.settings.data_root, c).test) for c in body.channels
        ]
        steps = min(body.steps or 10**9, min(lengths) - body.offset)
        if steps < 10:
            raise ValueError("offset leaves fewer than 10 steps to replay")
    sid = await create_session(
        state.factory,
        kind="replay" if body.kind == "replay" else "scenario",
        seed=seed,
        start=start,
        speed=body.speed,
        synthetic=body.kind == "scenario",
        scenario_id=scenario_id,
        config=body.model_dump(),
    )
    run = SessionRun(sid, body.kind, scenario_id, seed, body.speed, steps, start, labels=labels)
    state.sessions[sid] = run
    return run


async def run_session(state: AppState, run: SessionRun, body: Any) -> None:
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    try:
        if run.kind == "scenario":
            await _run_scenario(state, run, body)
        else:
            await _run_replay(state, run, body)
        run.status = "completed"
    except asyncio.CancelledError:
        run.status = "failed"
        run.error = "cancelled"
        raise
    except Exception as exc:  # the run must never take the API down; report and record
        run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        await _set_status(state, run)
        run.publish({"type": "status", "status": run.status, "error": run.error})
        run.publish({"type": "done"})


async def _set_status(state: AppState, run: SessionRun) -> None:
    async with state.factory() as s:
        row = await s.get(SessionRow, run.id)
        if row is not None:
            row.status = run.status
            await s.commit()


async def _run_scenario(state: AppState, run: SessionRun, body: Any) -> None:
    spec = instantiate(state.specs[run.scenario_id or ""], body.variant)
    batt, wheel = await asyncio.to_thread(state.trajectories)
    built = await asyncio.to_thread(
        build_scenario,
        spec,
        state.settings.data_root,
        state.settings.donki_cache,
        frame_key_from_env(),
        batt,
        wheel,
    )
    engine = await state.scenario_engine()
    await register_channels(state.factory, built.mission.catalog())
    ctx = FeatureContext(weather_covered=built.truth.weather_context != "unavailable")
    await _pump(state, run, built.mission, engine, list(built.weather), ctx, attribute=True)
    run.truth = built.truth
    async with state.factory() as s:
        t = built.truth
        s.add(
            GroundTruthRow(
                session_id=run.id,
                scenario_id=t.scenario_id,
                klass=t.klass.value,
                subtype=t.subtype,
                channels=t.channels,
                tags={"sparta": t.sparta, "attack": t.attack},
                start_ts=run.start + timedelta(seconds=(t.start_step or 0) * STEP_SECONDS),
                end_ts=run.start + timedelta(seconds=(t.end_step or 0) * STEP_SECONDS),
            )
        )
        await s.commit()


async def _run_replay(state: AppState, run: SessionRun, body: Any) -> None:
    channels = list(body.channels)
    mission = Mission(
        MissionConfig(
            seed=0,
            start=QUIET_START,
            frame_key=frame_key_from_env(),
            smap_channels=tuple(channels),
            include_bus=False,
            data_root=state.settings.data_root,
            smap_offset=body.offset,
        )
    )
    engine = await asyncio.to_thread(state.replay_engine, channels)
    await register_channels(state.factory, mission.catalog())
    await _pump(state, run, mission, engine, [], FeatureContext(False), attribute=False)


async def _pump(
    state: AppState,
    run: SessionRun,
    mission: Mission,
    engine: DetectionEngine,
    weather: list[Any],
    ctx: FeatureContext,
    attribute: bool,
) -> None:
    sink = DbSink(state.factory, run.id, mission.config.start)
    ing = Ingestor(mission.apids, mission.config.frame_key, mission.config.start)
    builder = IncidentBuilder()
    replay = ReplayEngine(mission, speed=run.speed, max_steps=run.steps)
    run.replay = replay
    last_pub: dict[tuple[str, str | None], int] = {}
    counter = {"n": 0}

    async def close(windows: list[IncidentWindow]) -> None:
        for w in windows:
            await _finalize(state, run, w, ctx, attribute, counter)

    for w in weather:
        for o in engine.process(w):
            builder.add(o)
    async for tick in replay.stream():
        run.position = tick.k + 1
        values: dict[str, float | None] = {}
        for e in ing.process_tick(tick):
            await sink(e)
            if isinstance(e, TelemetryEvent):
                values[e.channel] = _finite(e.value)
            for o in engine.process(e):
                builder.add(o)
                _notify(run, o, tick.k, last_pub)
        run.publish({"type": "samples", "k": tick.k, "ts": tick.ts, "values": values})
        await close(builder.advance(tick.ts))
    for o in engine.flush():
        builder.add(o)
    await close(builder.flush())
    await sink.flush()


def _notify(
    run: SessionRun, o: DetectorOutput, k: int, last: dict[tuple[str, str | None], int]
) -> None:
    if not o.fired:
        return
    key = (o.detector, o.channel)
    if k - last.get(key, -PUBLISH_EVERY) < PUBLISH_EVERY:
        return
    last[key] = k
    run.publish(
        {
            "type": "detection",
            "k": k,
            "detector": o.detector,
            "layer": o.layer.value,
            "channel": o.channel,
            "score": round(o.score, 3),
            "explanation": o.explanation,
        }
    )


async def _finalize(
    state: AppState,
    run: SessionRun,
    w: IncidentWindow,
    ctx: FeatureContext,
    attribute: bool,
    counter: dict[str, int],
) -> None:
    fired = w.fired
    peak = max((o.score for o in fired), default=0.0)
    span = (w.end - w.start) / STEP_SECONDS
    if attribute and state.model is not None:
        feats = extract(w, ctx)
        a = state.model.predict(feats)
        verdict, conf, post = a.verdict.value, a.confidence, a.posterior
        detail: dict[str, Any] = {
            "runner_up": a.runner_up,
            "for": [asdict(c) for c in a.evidence_for],
            "against": [asdict(c) for c in a.evidence_against],
        }
        version = a.model_version
    else:
        feats = {}
        verdict, conf, post, version = Verdict.NEEDS_HUMAN.value, 0.0, {}, "none"
        detail = {"note": "attribution is not applied to anonymized real SMAP/MSL channels"}
    iid = str(uuid.uuid4())
    opened = run.start + timedelta(seconds=w.start)
    async with state.factory() as s:
        s.add(
            Incident(
                id=iid,
                session_id=run.id,
                opened_at=opened,
                closed_at=run.start + timedelta(seconds=w.end),
                status="closed",
                verdict=verdict,
                confidence=conf,
                severity=severity_of(verdict, peak, span),
                posterior=post,
                features=feats,
                top_evidence=detail,
                affected_channels=sorted({o.channel for o in fired if o.channel}),
                model_version=version,
                synthetic=run.kind == "scenario",
            )
        )
        rows = []
        for o in fired[:MAX_STORED_OUTPUTS]:
            counter["n"] += 1
            rows.append(
                {
                    "session_id": run.id,
                    "ts": run.start + timedelta(seconds=o.ts),
                    "n": counter["n"],
                    "detector": o.detector,
                    "layer": o.layer.value,
                    "channel_id": o.channel,
                    "score": o.score,
                    "fired": o.fired,
                    "evidence": [e.model_dump() for e in o.evidence],
                    "explanation": o.explanation,
                }
            )
        if rows:
            await s.execute(insert(DetectorOutputRow), rows)
        await s.commit()
    run.incidents += 1
    run.publish(
        {
            "type": "incident",
            "id": iid,
            "verdict": verdict,
            "confidence": round(conf, 3),
            "severity": severity_of(verdict, peak, span),
            "opened_step": round(w.start / STEP_SECONDS),
            "closed_step": round(w.end / STEP_SECONDS),
        }
    )
