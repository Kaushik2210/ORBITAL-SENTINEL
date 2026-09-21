from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")

from sentinel_core.detection.l2 import ForecasterDetector
from sentinel_core.events import TelemetryEvent
from sentinel_ml import forecaster as fc
from tests.ml.test_forecaster import toy


@pytest.fixture(scope="module")
def trained(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, fc.TrainResult]:
    v, cmd = toy()
    res = fc.train_channel(v, cmd, seed=1, max_epochs=10)
    d = tmp_path_factory.mktemp("onnx")
    fc.export_onnx(res.model, res.n_in, d / "X-1.onnx")
    return d, res


def events(v: np.ndarray, cmd: np.ndarray) -> list[TelemetryEvent]:
    masks = (cmd * (1 << np.arange(cmd.shape[1]))).sum(axis=1).astype(int)
    return [
        TelemetryEvent(k * 60.0, "X-1", float(x), int(m), ts_rx=k * 60.0)
        for k, (x, m) in enumerate(zip(v, masks, strict=True))
    ]


def test_streaming_error_matches_the_batch_error_exactly(
    trained: tuple[Path, fc.TrainResult],
) -> None:
    d, res = trained
    v, cmd = toy(seed=9)
    det = ForecasterDetector(d)
    for e in events(v, cmd):
        det.update(e)
    batch = fc.smoothed_errors(v, fc.predict(res.model, v, cmd))
    assert det.last_error("X-1") == pytest.approx(float(batch[-1]), abs=1e-3)


def test_nominal_data_is_quiet_and_an_injected_anomaly_fires(
    trained: tuple[Path, fc.TrainResult],
) -> None:
    d, _ = trained
    v, cmd = toy(seed=9)
    det = ForecasterDetector(d)
    quiet = [o for e in events(v, cmd) for o in det.update(e) if o.fired]
    bad = v.copy()
    bad[300:340] += 1.5
    det2 = ForecasterDetector(d)
    hits = [o for e in events(bad, cmd) for o in det2.update(e) if o.fired]
    assert len(hits) > len(quiet)
    assert any(300 <= o.ts / 60 <= 360 for o in hits)
    assert hits[0].layer.value == "L2"
    assert {e.name for e in hits[0].evidence} >= {"smoothed_error", "predicted", "observed"}


def test_unknown_channels_and_nan_are_ignored_and_reset_clears_state(
    trained: tuple[Path, fc.TrainResult],
) -> None:
    d, _ = trained
    det = ForecasterDetector(d)
    assert det.update(TelemetryEvent(0.0, "NOPE", 1.0)) == []
    assert det.update(TelemetryEvent(0.0, "X-1", float("nan"))) == []
    v, cmd = toy(seed=2)
    for e in events(v, cmd)[:120]:
        det.update(e)
    assert det.last_error("X-1") > 0
    det.reset()
    assert det.last_error("X-1") == 0.0
