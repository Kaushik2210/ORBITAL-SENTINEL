"""The FastAPI application: REST + WebSocket + SSE over the detection core.

Authentication is a JWT bearer token (``POST /auth/login``) with three roles (viewer < analyst <
admin); see ``security/deps.py``. With ``PUBLIC_DEMO_MODE=true`` (the default), unauthenticated
``GET`` requests are allowed at the ``viewer`` level so the platform can be browsed without an
account — everything that starts a session, calls the agent, or reads the audit log always needs a
real token. Ground truth is withheld until a session has finished (``409`` before that), so a
"guess the cause" front end cannot leak the answer through the API.
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
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select

from sentinel_agent.client import investigate as run_investigation
from sentinel_core import __version__
from sentinel_sim import bus, smap_msl
from sentinel_sim.donki import DonkiClient

from .db.models import AuditLog, DetectorOutputRow, Incident, InvestigationEvent, Telemetry
from .db.models import GroundTruth as GroundTruthRow
from .db.models import Report as ReportRow
from .db.models import Session as SessionRow
from .db.repo import get_user_by_email
from .investigate import build_case, report_markdown
from .runner import create_run, run_session
from .schemas import (
    AuditLogOut,
    AuditVerifyOut,
    ChannelOut,
    ContributionOut,
    Control,
    EvidenceOut,
    GroundTruthOut,
    Health,
    IncidentOut,
    InvestigateOut,
    LoginRequest,
    MeOut,
    Page,
    Ready,
    ReportOut,
    ScenarioOut,
    SessionCreate,
    SessionOut,
    TelemetryPoint,
    TelemetrySeries,
    TokenOut,
)
from .security.deps import optional_principal, principal_from, require_role
from .security.headers import security_headers
from .security.passwords import verify_password
from .security.ratelimit import RateLimitMiddleware
from .security.tokens import DEFAULT_TTL, Principal, issue_token
from .settings import Settings
from .state import AppState, SessionRun

_view = require_role("viewer")
_analyst = require_role("analyst")
_admin = require_role("admin")

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
    app.middleware("http")(security_headers)
    app.add_middleware(RateLimitMiddleware, limit_per_minute=cfg.rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
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

    # ------------------------------------------------------------------ auth
    @r.post("/auth/login", response_model=TokenOut)
    async def login(request: Request, body: LoginRequest) -> TokenOut:
        s = _state(request)
        user = await get_user_by_email(s.factory, body.email)
        ok = user is not None and verify_password(body.password, user.password_hash)
        await s.audit.record(
            actor=body.email, action="login", target="auth", detail={"success": ok}
        )
        if not ok or user is None:
            raise HTTPException(401, "invalid email or password")
        token = issue_token(user.email, user.role, s.settings.jwt_secret)
        return TokenOut(
            access_token=token,
            role=user.role,
            expires_in_seconds=int(DEFAULT_TTL.total_seconds()),
        )

    @r.get("/auth/me", response_model=MeOut)
    async def me(principal: Principal | None = Depends(optional_principal)) -> MeOut:
        if principal is None:
            raise HTTPException(401, "authentication required")
        return MeOut(email=principal.email, role=principal.role)

    # ------------------------------------------------------------------ catalog
    @r.get("/channels", response_model=list[ChannelOut])
    async def channels(_p: Principal = Depends(_view)) -> list[ChannelOut]:
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
    async def scenarios(
        request: Request, reveal: bool = False, _p: Principal = Depends(_view)
    ) -> list[ScenarioOut]:
        return [_scenario_out(s, reveal) for s in _state(request).specs.values()]

    @r.get("/scenarios/{sid}", response_model=ScenarioOut, response_model_exclude_none=True)
    async def scenario(
        request: Request, sid: str, reveal: bool = False, _p: Principal = Depends(_view)
    ) -> ScenarioOut:
        spec = _state(request).specs.get(sid)
        if spec is None:
            raise HTTPException(404, "unknown scenario")
        return _scenario_out(spec, reveal)

    # ------------------------------------------------------------------ sessions
    @r.post("/sessions", response_model=SessionOut, status_code=202)
    async def create_session_ep(
        request: Request, body: SessionCreate, principal: Principal = Depends(_analyst)
    ) -> SessionOut:
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
        await s.audit.record(
            actor=principal.email,
            action="session.create",
            target=run.id,
            detail={"kind": run.kind, "scenario_id": run.scenario_id},
        )
        return _session_out(run)

    @r.get("/sessions", response_model=list[SessionOut])
    async def sessions(request: Request, _p: Principal = Depends(_view)) -> list[SessionOut]:
        return [_session_out(x) for x in _state(request).sessions.values()]

    @r.get("/sessions/{sid}", response_model=SessionOut)
    async def session(request: Request, sid: str, _p: Principal = Depends(_view)) -> SessionOut:
        return _session_out(_run(_state(request), sid))

    @r.post("/sessions/{sid}/control", response_model=SessionOut)
    async def control(
        request: Request, sid: str, body: Control, principal: Principal = Depends(_analyst)
    ) -> SessionOut:
        s = _state(request)
        run = _run(s, sid)
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
        await s.audit.record(
            actor=principal.email,
            action="session.control",
            target=sid,
            detail={"action": body.action},
        )
        return _session_out(run)

    @r.get("/sessions/{sid}/telemetry", response_model=list[TelemetrySeries])
    async def telemetry(
        request: Request,
        sid: str,
        channels: str = Query(max_length=400),
        max_points: int = Query(1500, ge=10, le=10000),
        _p: Principal = Depends(_view),
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
    async def ground_truth(
        request: Request, sid: str, _p: Principal = Depends(_view)
    ) -> GroundTruthOut:
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
        _p: Principal = Depends(_view),
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
    async def incident(request: Request, iid: str, _p: Principal = Depends(_view)) -> IncidentOut:
        async with _state(request).factory() as db:
            row = await db.get(Incident, iid)
        if row is None:
            raise HTTPException(404, "unknown incident")
        return _incident_out(row)

    @r.get("/incidents/{iid}/evidence", response_model=EvidenceOut)
    async def evidence(
        request: Request,
        iid: str,
        limit: int = Query(100, ge=1, le=300),
        _p: Principal = Depends(_view),
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

    @r.post("/incidents/{iid}/investigate", response_model=InvestigateOut)
    async def investigate(
        request: Request, iid: str, principal: Principal = Depends(_analyst)
    ) -> InvestigateOut:
        """Run the AI investigation agent over an incident's stored evidence and persist the report.

        Falls back to the deterministic offline report unless ``ANTHROPIC_API_KEY`` is set; see
        docs/API.md. Re-running replaces the previously stored report and trace for this incident.
        """
        s = _state(request)
        case = await build_case(s.factory, iid)
        if case is None:
            raise HTTPException(404, "unknown incident")
        report, trace, mode = await asyncio.to_thread(
            run_investigation,
            case,
            api_key=s.settings.anthropic_api_key,
            model=s.settings.anthropic_model,
        )
        model = s.settings.anthropic_model if mode == "llm" else None
        now = datetime.now(UTC)
        md = report_markdown(report)
        async with s.factory() as db:
            await db.merge(
                ReportRow(
                    incident_id=iid,
                    generated_at=now,
                    mode=mode,
                    model=model,
                    report=report.model_dump(mode="json"),
                    markdown=md,
                )
            )
            await db.execute(
                delete(InvestigationEvent).where(InvestigationEvent.incident_id == iid)
            )
            for i, ev in enumerate(trace.events):
                db.add(
                    InvestigationEvent(incident_id=iid, seq=i, ts=now, kind=ev["kind"], payload=ev)
                )
            await db.commit()
        await s.audit.record(
            actor=principal.email, action="incident.investigate", target=iid, detail={"mode": mode}
        )
        return InvestigateOut(
            incident_id=iid,
            mode=mode,
            model=model,
            report=ReportOut(**report.model_dump(mode="json")),
            markdown=md,
            trace=trace.events,
            generated_at=now,
        )

    @r.get("/incidents/{iid}/report", response_model=InvestigateOut)
    async def get_report(
        request: Request, iid: str, _p: Principal = Depends(_view)
    ) -> InvestigateOut:
        async with _state(request).factory() as db:
            row = await db.get(ReportRow, iid)
            if row is None:
                raise HTTPException(404, "no report yet; POST .../investigate first")
            events = (
                (
                    await db.execute(
                        select(InvestigationEvent)
                        .where(InvestigationEvent.incident_id == iid)
                        .order_by(InvestigationEvent.seq)
                    )
                )
                .scalars()
                .all()
            )
        return InvestigateOut(
            incident_id=iid,
            mode=row.mode,
            model=row.model,
            report=ReportOut(**row.report),
            markdown=row.markdown,
            trace=[e.payload for e in events],
            generated_at=_utc(row.generated_at),
        )

    # ------------------------------------------------------------------ evaluation and datasets
    @r.get("/evaluation")
    async def evaluation(_p: Principal = Depends(_view)) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("evaluation_v1", "smap_msl_l1_v1", "smap_msl_l2_v1"):
            p = cfg.docs_data_dir / f"{name}.json"
            if p.is_file():
                out[name] = json.loads(p.read_text(encoding="utf-8"))
        if not out:
            raise HTTPException(404, "no evaluation runs found")
        return out

    @r.get("/datasets/smap-msl")
    async def smap_msl_labels(_p: Principal = Depends(_view)) -> list[dict[str, Any]]:
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
        _p: Principal = Depends(_view),
    ) -> list[dict[str, Any]]:
        client = DonkiClient(cfg.donki_cache)
        if not client.has_coverage(kind, start, end):
            raise HTTPException(404, "that range is not in the local DONKI cache")
        return client.get_events(kind, start, end, allow_network=False)

    # ------------------------------------------------------------------ audit log (admin only)
    @r.get("/audit", response_model=Page[AuditLogOut])
    async def audit_log(
        request: Request,
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
        _p: Principal = Depends(_admin),
    ) -> Page[AuditLogOut]:
        s = _state(request)
        offset = _cursor(cursor)
        async with s.factory() as db:
            rows = (
                (
                    await db.execute(
                        select(AuditLog)
                        .order_by(AuditLog.id.desc())
                        .offset(offset)
                        .limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
        page = [
            AuditLogOut(
                id=x.id,
                ts=_utc(x.ts),
                actor=x.actor,
                action=x.action,
                target=x.target,
                detail=x.detail,
            )
            for x in rows[:limit]
        ]
        return Page(items=page, next_cursor=str(offset + limit) if len(rows) > limit else None)

    @r.get("/audit/verify", response_model=AuditVerifyOut)
    async def audit_verify(request: Request, _p: Principal = Depends(_admin)) -> AuditVerifyOut:
        ok, bad = await _state(request).audit.verify()
        return AuditVerifyOut(ok=ok, first_bad_row=bad)

    # ------------------------------------------------------------------ live streams
    @app.websocket(f"{API}/ws/sessions/{{sid}}")
    async def ws_session(ws: WebSocket, sid: str) -> None:
        state: AppState = ws.app.state.sentinel
        principal = principal_from(ws, state.settings.jwt_secret)
        if principal is None and not state.settings.public_demo_mode:
            await ws.close(code=4401)  # policy violation: authentication required
            return
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
        s = _state(request)
        principal = principal_from(request, s.settings.jwt_secret)
        if principal is None and not s.settings.public_demo_mode:
            raise HTTPException(401, "authentication required")
        run = _run(s, sid)

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
