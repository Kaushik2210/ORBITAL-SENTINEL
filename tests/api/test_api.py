from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel_api.app import create_app
from sentinel_api.settings import Settings

pytestmark = pytest.mark.slow


def settings(tmp: Path, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{(tmp / 'api.sqlite').as_posix()}",
        "data_root": Path("nonexistent"),  # no real data: labeled synthetic-parametric trajectories
        "l2_models_dir": Path("nonexistent"),
        "donki_cache": tmp / "donki",
        "cors_origins": ["http://localhost:3000"],
        "anthropic_api_key": None,  # force the offline agent fallback; no network in tests
    }
    base.update(over)
    return Settings(**base)


def wait_done(c: TestClient, sid: str, timeout: float = 180.0) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        body: dict[str, Any] = c.get(f"/api/v1/sessions/{sid}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.25)
    raise AssertionError("session did not finish")


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    with TestClient(create_app(settings(tmp_path_factory.mktemp("api")))) as c:
        yield c


@pytest.fixture(scope="module")
def finished(client: TestClient) -> dict[str, Any]:
    r = client.post("/api/v1/sessions", json={"scenario_id": "auth_bruteforce", "speed": 0})
    assert r.status_code == 202
    return wait_done(client, r.json()["id"])


# ------------------------------------------------------------------ catalog and health


def test_health_ready_and_channels(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    ready = client.get("/api/v1/ready").json()
    assert ready["ready"] is True
    assert ready["attribution_model"]
    assert ready["real_data"] is False
    chans = client.get("/api/v1/channels").json()
    assert len(chans) == 14
    assert all(c["synthetic"] for c in chans)


def test_scenarios_hide_the_answer_unless_revealed(client: TestClient) -> None:
    hidden = client.get("/api/v1/scenarios").json()
    assert len(hidden) == 33
    assert set(hidden[0]) == {"id", "steps"}
    shown = client.get("/api/v1/scenarios/spoof_battv_clean", params={"reveal": True}).json()
    assert shown["class"] == "cyberattack"
    assert shown["subtype"] == "sensor_spoofing"
    assert "narrative" not in client.get("/api/v1/scenarios/spoof_battv_clean").json()
    assert client.get("/api/v1/scenarios/nope").status_code == 404


def test_openapi_documents_the_api(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/api/v1/sessions" in spec["paths"]
    assert "/api/v1/incidents/{iid}/evidence" in spec["paths"]


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    "body",
    [
        {"scenario_id": "auth_bruteforce", "surprise": 1},
        {"scenario_id": "Auth Bruteforce!"},
        {"scenario_id": "auth_bruteforce", "speed": -1},
        {"scenario_id": "auth_bruteforce", "variant": 99},
        {"kind": "teleport"},
        {"scenario_id": "x" * 500},
    ],
)
def test_bad_session_requests_are_rejected_before_anything_runs(
    client: TestClient, body: dict[str, Any]
) -> None:
    assert client.post("/api/v1/sessions", json=body).status_code == 422


def test_unknown_scenario_and_missing_real_data(client: TestClient) -> None:
    assert client.post("/api/v1/sessions", json={"scenario_id": "no_such_thing"}).status_code == 404
    r = client.post("/api/v1/sessions", json={"kind": "replay", "channels": ["P-1"]})
    assert r.status_code == 409
    assert "not downloaded" in r.json()["detail"]
    assert client.get("/api/v1/sessions/nope").status_code == 404
    assert client.get("/api/v1/incidents", params={"cursor": "abc"}).status_code == 422


def test_cors_only_allows_the_configured_origin(client: TestClient) -> None:
    ok = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


# ------------------------------------------------------------------ a full run


def test_a_scenario_runs_to_completion_and_raises_a_correct_incident(
    client: TestClient, finished: dict[str, Any]
) -> None:
    assert finished["status"] == "completed", finished["error"]
    assert finished["position"] == finished["steps"] == 1800
    assert finished["synthetic"] is True
    page = client.get("/api/v1/incidents", params={"session_id": finished["id"]}).json()
    assert page["items"]
    top = page["items"][0]
    assert top["verdict"] == "cyberattack"
    assert top["severity"] in ("high", "critical")
    assert top["synthetic"] is True
    assert abs(sum(top["posterior"].values()) - 1.0) < 1e-6


def test_evidence_explains_the_decision_from_stored_data(
    client: TestClient, finished: dict[str, Any]
) -> None:
    iid = client.get(
        "/api/v1/incidents", params={"session_id": finished["id"], "verdict": "cyberattack"}
    ).json()["items"][0]["id"]
    ev = client.get(f"/api/v1/incidents/{iid}/evidence").json()
    assert ev["features"]["auth_anomaly"] > 0.5
    assert ev["evidence_for"]
    assert all(c["contribution"] > 0 for c in ev["evidence_for"])
    assert all(c["contribution"] < 0 for c in ev["evidence_against"])
    assert any(o["detector"] == "l4.auth_anomaly" for o in ev["detector_outputs"])
    assert client.get("/api/v1/incidents/nope/evidence").status_code == 404


def test_the_stored_decision_is_reproducible_from_the_stored_features(
    client: TestClient, finished: dict[str, Any]
) -> None:
    from sentinel_core.attribution.model import AttributionModel

    inc = client.get("/api/v1/incidents", params={"session_id": finished["id"]}).json()["items"][0]
    ev = client.get(f"/api/v1/incidents/{inc['id']}/evidence").json()
    model = AttributionModel.load(Path("models/attribution-v1.json"))
    again = model.predict(ev["features"])
    assert again.model_version == inc["model_version"]
    assert again.posterior == pytest.approx(inc["posterior"])


def test_incident_filters_and_pagination(client: TestClient, finished: dict[str, Any]) -> None:
    sid = finished["id"]
    assert (
        client.get(
            "/api/v1/incidents", params={"session_id": sid, "verdict": "environmental"}
        ).json()["items"]
        == []
    )
    assert (
        client.get("/api/v1/incidents", params={"session_id": sid, "min_confidence": 1.0}).json()[
            "items"
        ]
        == []
    )
    total = len(client.get("/api/v1/incidents", params={"session_id": sid}).json()["items"])
    first = client.get("/api/v1/incidents", params={"session_id": sid, "limit": 1}).json()
    assert len(first["items"]) == 1
    if total > 1:
        assert first["next_cursor"] == "1"
        second = client.get(
            "/api/v1/incidents",
            params={"session_id": sid, "limit": 1, "cursor": first["next_cursor"]},
        ).json()
        assert second["items"][0]["id"] != first["items"][0]["id"]
    else:
        assert first["next_cursor"] is None


def test_telemetry_is_downsampled_and_flags_synthetic(
    client: TestClient, finished: dict[str, Any]
) -> None:
    r = client.get(
        f"/api/v1/sessions/{finished['id']}/telemetry",
        params={"channels": "batt_v_a,rw1_vib_a", "max_points": 200},
    )
    assert r.status_code == 200
    series = {s["channel"]: s for s in r.json()}
    assert set(series) == {"batt_v_a", "rw1_vib_a"}
    pts = series["batt_v_a"]["points"]
    assert 100 <= len(pts) <= 200
    assert series["batt_v_a"]["synthetic"] is True
    assert all(p["value"] is not None for p in pts)
    assert pts[0]["step"] < pts[-1]["step"]


def test_ground_truth_is_revealed_only_after_the_run(
    client: TestClient, finished: dict[str, Any]
) -> None:
    gt = client.get(f"/api/v1/sessions/{finished['id']}/ground-truth").json()
    assert gt["class"] == "cyberattack"
    assert gt["subtype"] == "abnormal_authentication"
    assert gt["start_step"] == 800
    assert gt["attack"] == ["T1110"]


def test_control_on_a_finished_session_is_a_conflict(
    client: TestClient, finished: dict[str, Any]
) -> None:
    r = client.post(f"/api/v1/sessions/{finished['id']}/control", json={"action": "pause"})
    assert r.status_code == 409


# ------------------------------------------------------------------ live streams


def test_sse_replays_incidents_detections_and_completion(
    client: TestClient, finished: dict[str, Any]
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", f"/api/v1/sse/sessions/{finished['id']}") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        name = ""
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
                if name == "done":
                    break
    kinds = [k for k, _ in events]
    assert "incident" in kinds
    assert "detection" in kinds
    assert kinds[-1] == "done"
    assert "samples" not in kinds


def test_websocket_streams_status_and_incidents(
    client: TestClient, finished: dict[str, Any]
) -> None:
    msgs = []
    with client.websocket_connect(f"/api/v1/ws/sessions/{finished['id']}") as ws:
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "done":
                break
    assert {m["type"] for m in msgs} >= {"status", "incident", "done"}
    with (
        pytest.raises(Exception),  # noqa: B017, PT011 - the close code surfaces as a disconnect
        client.websocket_connect("/api/v1/ws/sessions/unknown"),
    ):
        pass


# ------------------------------------------------------------------ investigation agent


def test_investigate_runs_offline_without_a_key_and_persists_the_report(
    client: TestClient, finished: dict[str, Any]
) -> None:
    items = client.get("/api/v1/incidents", params={"session_id": finished["id"]}).json()["items"]
    iid = items[0]["id"]
    r = client.post(f"/api/v1/incidents/{iid}/investigate")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "offline"
    assert body["model"] is None
    assert body["report"]["verdict"]
    assert 0.0 <= body["report"]["confidence"] <= 1.0
    assert body["report"]["key_evidence"]
    assert "# Investigation report" in body["markdown"]
    assert body["trace"]  # at least the "no API key" note

    fetched = client.get(f"/api/v1/incidents/{iid}/report").json()
    assert fetched["report"] == body["report"]
    assert fetched["trace"] == body["trace"]


def test_investigate_on_an_unknown_incident_is_404(client: TestClient) -> None:
    assert client.post("/api/v1/incidents/nope/investigate").status_code == 404


def test_report_before_investigating_is_404(client: TestClient, finished: dict[str, Any]) -> None:
    items = client.get("/api/v1/incidents", params={"session_id": finished["id"]}).json()["items"]
    if len(items) < 2:
        pytest.skip("needs a second incident that the earlier investigate test did not touch")
    assert client.get(f"/api/v1/incidents/{items[-1]['id']}/report").status_code == 404


def test_evaluation_endpoint_serves_the_saved_runs(client: TestClient) -> None:
    ev = client.get("/api/v1/evaluation").json()
    assert "evaluation_v1" in ev
    assert ev["evaluation_v1"]["meta"]["n_runs"] == 462


# ------------------------------------------------------------------ live pacing and limits


def test_ground_truth_is_withheld_while_running_and_concurrency_is_limited(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path, max_concurrent_sessions=1))) as c:
        first = c.post("/api/v1/sessions", json={"scenario_id": "nominal_a", "speed": 600}).json()
        assert c.get(f"/api/v1/sessions/{first['id']}/ground-truth").status_code == 409
        second = c.post("/api/v1/sessions", json={"scenario_id": "nominal_b", "speed": 600})
        assert second.status_code == 429
        paused = c.post(
            f"/api/v1/sessions/{first['id']}/control", json={"action": "speed", "speed": 100000}
        )
        assert paused.status_code in (200, 409)  # 409 if it is still calibrating
