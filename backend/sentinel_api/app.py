"""The FastAPI application: REST + WebSocket + SSE over the detection core.

Not yet implemented (Phase 9): authentication, roles, rate limiting, audit logging. Until then every
endpoint is unauthenticated and the API must only be run locally or behind a trusted proxy.
Ground truth is withheld until a session has finished (``409`` before that), so a "guess the cause"
front end cannot leak the answer through the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from sentinel_core import __version__
from sentinel_sim import bus, smap_msl
from sentinel_sim.donki import DonkiClient

from .db.models import DetectorOutputRow, Incident, Telemetry
from .db.models import GroundTruth as GroundTruthRow
from .db.models import Session as SessionRow
from .runner import create_run, run_session
from .schemas import (
    ChannelOut,
    ContributionOut,
    Control,
    EvidenceOut,
    GroundTruthOut,
    Health,
    IncidentOut,
    Page,
    Ready,
    ScenarioOut,
    SessionCreate,
    SessionOut,
    TelemetryPoint,
    TelemetrySeries,
)
from .settings import Settings
from .state import AppState, SessionRun

API = "/api/v1"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.sentinel
    return state


def _run(state: AppState, sid: str) -> SessionRun:
    run = state.sessions.get(sid)
    if run is None:
        raise HTTPException(404, "unknown session")
    return run


def _incident_out(row: Incident) -> IncidentOut:
    return IncidentOut(
        id=row.id,
        session_id=row.session_id,
        opened_at=_utc(row.opened_at),
        closed_at=_utc(row.closed_at) if row.closed_at else None,
        status=row.status,
        verdict=row.verdict,
        confidence=row.confidence,
        severity=row.severity,
        posterior=row.posterior,
        affected_channels=list(row.affected_channels),
        model_version=row.model_version,
        synthetic=row.synthetic,
    )


def _session_out(r: SessionRun) -> SessionOut:
    return SessionOut(
        id=r.id,
        kind=r.kind,
        status=r.status,
        scenario_id=r.scenario_id,
        seed=r.seed,
        speed=r.speed,
        position=r.position,
        steps=r.steps,
        synthetic=r.kind == "scenario",
        start=r.start,
        incidents=r.incidents,
        error=r.error,
        labels=r.labels,
    )


def _cursor(cursor: str | None) -> int:
    try:
        return max(0, int(cursor)) if cursor else 0
    except ValueError as exc:
        raise HTTPException(422, "invalid cursor") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = AppState(cfg)
        app.state.sentinel = state
        await state.start()
        yield
        await state.stop()

    app = FastAPI(
        title="Orbital Sentinel API",
        version=__version__,
        description="Defensive spacecraft-cybersecurity simulation. All attacks are synthetic.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    r = APIRouter(prefix=API)

    # ------------------------------------------------------------------ health
    @r.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(version=__version__)

    @r.get("/ready", response_model=Ready)
    async def ready(request: Request) -> Ready:
        s = _state(request)
        l2 = len(list(cfg.l2_models_dir.glob("*.onnx"))) if cfg.l2_models_dir.is_dir() else 0
        return Ready(
            ready=s.model is not None,
            detection_engine=s._engine is not None,
            attribution_model=s.model.version if s.model else None,
            l2_models=l2,
            real_data=(cfg.data_root / "smap_msl" / "labeled_anomalies.csv").is_file(),
        )

    # ------------------------------------------------------------------ catalog
    @r.get("/channels", response_model=list[ChannelOut])
    async def channels() -> list[ChannelOut]:
        return [
            ChannelOut(
                id=c,
                family="eps" if c in bus.EPS_CHANNELS else "adcs",
                group="EPS" if c in bus.EPS_CHANNELS else "ADCS",
                unit=bus.UNITS[c],
                synthetic=True,
                group_is_synthetic_grouping=False,
            )
            for c in bus.BUS_CHANNELS
        ]

    @r.get("/scenarios", response_model=list[ScenarioOut], response_model_exclude_none=True)
    async def scenarios(request: Request, reveal: bool = False) -> list[ScenarioOut]:
        return [_scenario_out(s, reveal) for s in _state(request).specs.values()]

    @r.get("/scenarios/{sid}", response_model=ScenarioOut, response_model_exclude_none=True)
    async def scenario(request: Request, sid: str, reveal: bool = False) -> ScenarioOut:
        spec = _state(request).specs.get(sid)
        if spec is None:
            raise HTTPException(404, "unknown scenario")
        return _scenario_out(spec, reveal)

    # ------------------------------------------------------------------ sessions
    @r.post("/sessions", response_model=SessionOut, status_code=202)
    async def create_session_ep(request: Request, body: SessionCreate) -> SessionOut:
        s = _state(request)
        try:
            run = await create_run(s, body)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        except smap_msl.DataUnavailableError as exc:
            raise HTTPException(
                409, "real SMAP/MSL data is not downloaded (run `make data`)"
            ) from exc
        run.task = asyncio.create_task(run_session(s, run, body))
        return _session_out(run)

    @r.get("/sessions", response_model=list[SessionOut])
    async def sessions(request: Request) -> list[SessionOut]:
        return [_session_out(x) for x in _state(request).sessions.values()]

    @r.get("/sessions/{sid}", response_model=SessionOut)
    async def session(request: Request, sid: str) -> SessionOut:
        return _session_out(_run(_state(request), sid))

    @r.post("/sessions/{sid}/control", response_model=SessionOut)
    async def control(request: Request, sid: str, body: Control) -> SessionOut:
        run = _run(_state(request), sid)
        if run.replay is None or run.status not in ("running", "paused"):
            raise HTTPException(409, "session is not running")
        if body.action == "pause":
            run.replay.pause()
            run.status = "paused"
        elif body.action == "play":
            run.replay.play()
            run.status = "running"
        else:
            if body.speed is None:
                raise HTTPException(422, "speed is required")
            run.replay.set_speed(body.speed)
            run.speed = body.speed
        run.publish({"type": "status", "status": run.status, "speed": run.speed})
        return _session_out(run)

    @r.get("/sessions/{sid}/telemetry", response_model=list[TelemetrySeries])
    async def telemetry(
        request: Request,
        sid: str,
        channels: str = Query(max_length=400),
        max_points: int = Query(1500, ge=10, le=10000),
    ) -> list[TelemetrySeries]:
        s = _state(request)
        _run(s, sid)
        wanted = [c for c in channels.split(",") if c][:12]
        out: list[TelemetrySeries] = []
        async with s.factory() as db:
            sess = await db.get(SessionRow, sid)
            assert sess is not None
            for ch in wanted:
                rows = (
                    (
                        await db.execute(
                            select(Telemetry)
                            .where(Telemetry.session_id == sid, Telemetry.channel_id == ch)
                            .order_by(Telemetry.ts, Telemetry.n)
                        )
                    )
                    .scalars()
                    .all()
                )
                stride = max(1, -(-len(rows) // max_points))
                start = _utc(sess.start)
                pts = [
                    TelemetryPoint(
                        ts=_utc(x.ts),
                        step=round((_utc(x.ts) - start).total_seconds() / 60),
                        value=x.value if x.value == x.value else None,
                    )
                    for x in rows[::stride]
                ]
                out.append(
                    TelemetrySeries(
                        channel=ch, synthetic=bool(rows and rows[0].synthetic), points=pts
                    )
                )
        return out

    @r.get(
        "/sessions/{sid}/ground-truth", response_model=GroundTruthOut, response_model_by_alias=True
    )
    async def ground_truth(request: Request, sid: str) -> GroundTruthOut:
        s = _state(request)
        run = _run(s, sid)
        if run.kind != "scenario":
            raise HTTPException(404, "real-data replays have labels, not scenario ground truth")
        if run.status not in ("completed", "failed"):
            raise HTTPException(409, "ground truth is withheld until the session has finished")
        async with s.factory() as db:
            row = (
                await db.execute(select(GroundTruthRow).where(GroundTruthRow.session_id == sid))
            ).scalar_one_or_none()
        if row is None or run.truth is None:
            raise HTTPException(404, "no ground truth recorded")
        t = run.truth
        return GroundTruthOut(
            scenario_id=t.scenario_id,
            klass=t.klass.value,
            subtype=t.subtype,
            start_step=t.start_step,
            end_step=t.end_step,
            channels=t.channels,
            sparta=t.sparta,
            attack=t.attack,
            confusable_with=t.confusable_with,
            weather_context=t.weather_context,
            data_sources=t.data_sources,
        )

    # ------------------------------------------------------------------ incidents
    @r.get("/incidents", response_model=Page[IncidentOut])
    async def incidents(
        request: Request,
        session_id: str | None = None,
        verdict: str | None = None,
        severity: str | None = None,
        min_confidence: float = Query(0.0, ge=0.0, le=1.0),
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
    ) -> Page[IncidentOut]:
        s = _state(request)
        offset = _cursor(cursor)
        q = select(Incident).where(Incident.confidence >= min_confidence)
        if session_id:
            q = q.where(Incident.session_id == session_id)
        if verdict:
            q = q.where(Incident.verdict == verdict)
        if severity:
            q = q.where(Incident.severity == severity)
        async with s.factory() as db:
            rows = (
                (
                    await db.execute(
                        q.order_by(Incident.opened_at.desc(), Incident.id)
                        .offset(offset)
                        .limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
        page = [_incident_out(x) for x in rows[:limit]]
        return Page(items=page, next_cursor=str(offset + limit) if len(rows) > limit else None)

    @r.get("/incidents/{iid}", response_model=IncidentOut)
    async def incident(request: Request, iid: str) -> IncidentOut:
        async with _state(request).factory() as db:
            row = await db.get(Incident, iid)
        if row is None:
            raise HTTPException(404, "unknown incident")
        return _incident_out(row)

    @r.get("/incidents/{iid}/evidence", response_model=EvidenceOut)
    async def evidence(
        request: Request, iid: str, limit: int = Query(100, ge=1, le=300)
    ) -> EvidenceOut:
        async with _state(request).factory() as db:
            row = await db.get(Incident, iid)
            if row is None:
                raise HTTPException(404, "unknown incident")
            outs = (
                (
                    await db.execute(
                        select(DetectorOutputRow)
                        .where(
                            DetectorOutputRow.session_id == row.session_id,
                            DetectorOutputRow.ts >= row.opened_at,
                            DetectorOutputRow.ts <= (row.closed_at or row.opened_at),
                        )
                        .order_by(DetectorOutputRow.ts, DetectorOutputRow.n)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        d = row.top_evidence or {}
        return EvidenceOut(
            incident=_incident_out(row),
            features=row.features,
            evidence_for=[ContributionOut(**c) for c in d.get("for", [])],
            evidence_against=[ContributionOut(**c) for c in d.get("against", [])],
            runner_up=d.get("runner_up"),
            detector_outputs=[
                {
                    "ts": _utc(o.ts).isoformat(),
                    "detector": o.detector,
                    "layer": o.layer,
                    "channel": o.channel_id,
                    "score": o.score,
                    "explanation": o.explanation,
                    "evidence": o.evidence,
                }
                for o in outs
            ],
        )

    # ------------------------------------------------------------------ evaluation and datasets
    @r.get("/evaluation")
    async def evaluation() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("evaluation_v1", "smap_msl_l1_v1", "smap_msl_l2_v1"):
            p = cfg.docs_data_dir / f"{name}.json"
            if p.is_file():
                out[name] = json.loads(p.read_text(encoding="utf-8"))
        if not out:
            raise HTTPException(404, "no evaluation runs found")
        return out

    @r.get("/datasets/smap-msl")
    async def smap_msl_labels() -> list[dict[str, Any]]:
        try:
            labels = smap_msl.load_labels(cfg.data_root)
        except smap_msl.DataUnavailableError as exc:
            raise HTTPException(
                404, "real SMAP/MSL data is not downloaded (run `make data`)"
            ) from exc
        return [
            {
                "channel": c,
                "spacecraft": v.spacecraft,
                "intervals": [list(i) for i in v.intervals],
                "classes": list(v.classes),
                "label_rows": v.n_label_rows,
                "synthetic": False,
            }
            for c, v in sorted(labels.items())
        ]

    @r.get("/datasets/donki")
    async def donki(
        kind: str = Query(pattern="^(FLR|CME|GST|SEP|IPS)$"),
        start: date = Query(),
        end: date = Query(),
    ) -> list[dict[str, Any]]:
        client = DonkiClient(cfg.donki_cache)
        if not client.has_coverage(kind, start, end):
            raise HTTPException(404, "that range is not in the local DONKI cache")
        return client.get_events(kind, start, end, allow_network=False)

    # ------------------------------------------------------------------ live streams
    @app.websocket(f"{API}/ws/sessions/{{sid}}")
    async def ws_session(ws: WebSocket, sid: str) -> None:
        state: AppState = ws.app.state.sentinel
        run = state.sessions.get(sid)
        if run is None:
            await ws.close(code=4404)
            return
        await ws.accept()
        q = run.subscribe()
        try:
            while True:
                msg = await q.get()
                await ws.send_json(msg)
                if msg["type"] == "done":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            run.unsubscribe(q)
            with contextlib.suppress(RuntimeError):  # already closed by the client
                await ws.close()

    @r.get("/sse/sessions/{sid}")
    async def sse_session(request: Request, sid: str) -> StreamingResponse:
        run = _run(_state(request), sid)

        async def gen() -> AsyncIterator[str]:
            q = run.subscribe()
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if msg["type"] == "samples":
                        continue  # SSE: incidents, detections, status (samples use the WebSocket)
                    yield f"event: {msg['type']}\ndata: {json.dumps(msg)}\n\n"
                    if msg["type"] == "done":
                        return
            finally:
                run.unsubscribe(q)

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    app.include_router(r)
    return app


def _scenario_out(spec: Any, reveal: bool) -> ScenarioOut:
    if not reveal:
        return ScenarioOut(id=spec.id, steps=spec.steps)
    return ScenarioOut(
        id=spec.id,
        steps=spec.steps,
        title=spec.title,
        narrative=spec.narrative,
        klass=spec.klass.value,
        subtype=spec.subtype,
        hard=spec.hard,
        confusable_with=spec.confusable_with,
        sparta=spec.sparta,
        attack=spec.attack,
    )
