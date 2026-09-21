# API

FastAPI application in `backend/sentinel_api`. Run it locally with `python scripts/tasks.py serve`
(<http://localhost:8000>), interactive docs at `/docs`, OpenAPI at `/openapi.json`.

> **Unauthenticated.** JWT, roles, rate limiting and the audit log are Phase 9 and **not built yet**.
> Run the API only on localhost or behind a trusted proxy. Input is strictly validated (Pydantic v2, unknown
> fields rejected), CORS is an explicit allowlist (`CORS_ALLOW_ORIGINS`), and concurrent sessions are capped
> (`MAX_CONCURRENT_SESSIONS`, default 4).

All REST routes are under `/api/v1`. Timestamps are UTC. Everything derived from the simulator carries
`synthetic: true`; real SMAP/MSL replays carry `synthetic: false`.

## Sessions

A session is one run of the mission, either a **scenario** (synthetic bus with injected faults/attacks,
full detection engine, attribution) or a **replay** of real SMAP/MSL channels (L1, plus L2 if ONNX models
exist, plus packet checks).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/sessions` | Start a session. `202` with the session; it runs in the background |
| `GET` | `/sessions`, `/sessions/{id}` | Status, position, incident count (`labels` for real replays) |
| `POST` | `/sessions/{id}/control` | `{"action": "pause" \| "play" \| "speed", "speed": n}` (`409` if not running) |
| `GET` | `/sessions/{id}/telemetry?channels=a,b&max_points=1500` | Stored samples, downsampled |
| `GET` | `/sessions/{id}/ground-truth` | Scenario truth. **`409` until the session has finished** |

```bash
# a scenario, as fast as possible
curl -s -X POST localhost:8000/api/v1/sessions -H 'content-type: application/json' \
  -d '{"scenario_id": "auth_bruteforce", "variant": 0, "speed": 0}'

# a real SMAP/MSL replay (needs `python scripts/tasks.py data`)
curl -s -X POST localhost:8000/api/v1/sessions -H 'content-type: application/json' \
  -d '{"kind": "replay", "channels": ["E-2"], "speed": 0}'
```

`speed` is simulated seconds per wall second (`0` = unpaced; `60` = one 60 s step per real second).
`variant` (0-13) picks a seeded variant of the scenario (shifted timing, scaled magnitudes, new noise).

## Incidents

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/incidents` | Filters: `session_id`, `verdict`, `severity`, `min_confidence`; `limit` (<= 200) and `cursor` |
| `GET` | `/incidents/{id}` | Verdict, calibrated confidence, posterior over the five classes, affected channels |
| `GET` | `/incidents/{id}/evidence` | Exact per-feature contributions for/against, the stored feature vector, and the fired detector outputs with their evidence |

A stored decision is reproducible: `AttributionModel.load("models/attribution-v1.json").predict(features)` on
the stored `features` returns the stored posterior (this is tested).

Verdicts are `nominal`, `mechanical_failure`, `environmental`, `sensor_malfunction`, `cyberattack` or
`needs_human`. **Real-channel replays never receive a class:** the attribution model was trained on the
synthetic bus and is not validated on anonymized real channels, so those incidents are `needs_human` with a
note in the evidence. Severity is a documented mapping from the verdict (escalated one level for a saturated,
sustained `cyberattack`).

## Live streams

| Transport | Path | Carries |
|---|---|---|
| WebSocket | `/api/v1/ws/sessions/{id}` | `samples` (one message per step: `k`, `ts`, `values`), `detection`, `incident`, `status`, `done` |
| SSE | `/api/v1/sse/sessions/{id}` | `detection`, `incident`, `status`, `done` (no samples; 15 s keepalives) |

Late subscribers first receive the session's recent non-sample messages, so a client that connects after
the run still sees its incidents and completion. Slow consumers drop the oldest sample instead of blocking the run.

## Catalog, evaluation, datasets

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health`, `/ready` | Liveness; readiness reports the attribution model version, L2 model count, real-data availability |
| `GET` | `/channels` | The synthetic bus channels with units |
| `GET` | `/scenarios?reveal=false` | Scenario ids and steps only. With `reveal=true`: title, narrative, class, subtype, SPARTA/ATT&CK tags |
| `GET` | `/evaluation` | The saved evaluation JSON (attribution, real-data L1, real-data L2) |
| `GET` | `/datasets/smap-msl` | Real labeled anomaly intervals per channel (`404` if data not downloaded) |
| `GET` | `/datasets/donki?kind=FLR&start=&end=` | Cached DONKI events (`404` if the range is not cached) |

## Configuration

Environment variables (see `.env.example`): `DATABASE_URL`, `DATA_ROOT`, `SCENARIOS_DIR`, `ATTRIBUTION_MODEL`,
`L2_MODELS_DIR`, `DOCS_DATA_DIR`, `DONKI_CACHE`, `CORS_ALLOW_ORIGINS`, `CALIBRATION_STEPS`,
`MAX_CONCURRENT_SESSIONS`. The detection engine is calibrated once, on the first scenario session (about 10 s),
then copied per session.

## Not implemented

Authentication and roles, rate limiting, audit logging, the investigation-agent endpoints
(`/incidents/{id}/investigate`, report export), dataset endpoints beyond the two above, and session
deletion/retention. These are tracked in `docs/PROGRESS.md`.
