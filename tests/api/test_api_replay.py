"""Real-data replay through the API (L1 + ONNX L2). Skipped without the data and models."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinel_api.app import create_app
from sentinel_api.settings import Settings
from sentinel_sim.scenarios.spec import QUIET_START

from .test_api import login

ROOT = Path("data/raw")
ONNX = Path("ml/runs/l2-v1/onnx")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (ROOT / "smap_msl" / "labeled_anomalies.csv").is_file(),
        reason="real data not downloaded",
    ),
]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    tmp = tmp_path_factory.mktemp("replay")
    cfg = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp / 'r.sqlite').as_posix()}",
        data_root=ROOT,
        l2_models_dir=ONNX,
        donki_cache=tmp / "donki",
        rate_limit_per_minute=0,  # this fixture polls a real replay to completion; don't self-block
    )
    with TestClient(create_app(cfg)) as c:
        login(c, cfg)
        yield c


def finish(c: TestClient, sid: str) -> dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < 240:
        body: dict[str, Any] = c.get(f"/api/v1/sessions/{sid}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.25)
    raise AssertionError("replay did not finish")


def test_dataset_explorer_lists_the_real_labels(client: TestClient) -> None:
    rows = client.get("/api/v1/datasets/smap-msl").json()
    assert len(rows) == 81  # 81 labeled channels (T-10 has no label row)
    assert {r["spacecraft"] for r in rows} == {"SMAP", "MSL"}
    assert all(r["synthetic"] is False for r in rows)
    assert next(r for r in rows if r["channel"] == "P-2")["label_rows"] == 2


def test_real_channel_replay_detects_a_labeled_anomaly_and_never_claims_a_class(
    client: TestClient,
) -> None:
    r = client.post("/api/v1/sessions", json={"kind": "replay", "channels": ["E-2"], "speed": 0})
    assert r.status_code == 202
    done = finish(client, r.json()["id"])
    assert done["status"] == "completed", done["error"]
    assert done["synthetic"] is False
    assert done["labels"]["E-2"] == [[5598, 6995]]  # the real labeled interval, in replay steps
    incidents = client.get("/api/v1/incidents", params={"session_id": done["id"]}).json()["items"]
    assert incidents
    assert all(i["verdict"] == "needs_human" for i in incidents)  # attribution is not applied here
    assert all(i["synthetic"] is False for i in incidents)
    ev = client.get(f"/api/v1/incidents/{incidents[0]['id']}/evidence").json()
    detectors = {o["detector"] for o in ev["detector_outputs"]}
    assert any(d.startswith(("l1.statistical", "l2.forecaster")) for d in detectors)

    def step(iso: str) -> float:
        return (datetime.fromisoformat(iso) - QUIET_START).total_seconds() / 60

    labeled = done["labels"]["E-2"][0]
    overlapping = [
        i
        for i in incidents
        if step(i["opened_at"]) <= labeled[1] and step(i["closed_at"]) >= labeled[0]
    ]
    assert overlapping, "no incident overlaps the real labeled anomaly"


def test_replay_validates_channels_and_offset(client: TestClient) -> None:
    assert (
        client.post("/api/v1/sessions", json={"kind": "replay", "channels": ["NOPE-1"]}).status_code
        == 404
    )
    assert (
        client.post("/api/v1/sessions", json={"kind": "replay", "channels": []}).status_code == 422
    )
    assert (
        client.post(
            "/api/v1/sessions", json={"kind": "replay", "channels": ["E-2"], "offset": 99999}
        ).status_code
        == 422
    )


@pytest.mark.skipif(not ONNX.is_dir(), reason="L2 models not trained")
def test_l2_is_reported_ready(client: TestClient) -> None:
    assert client.get("/api/v1/ready").json()["l2_models"] >= 1
