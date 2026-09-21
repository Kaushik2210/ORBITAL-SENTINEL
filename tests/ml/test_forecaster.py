from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")

from sentinel_ml import forecaster as fc


def toy(n: int = 500, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """A sine plus a step whenever command 0 is active, with multi-hot commands (2 columns)."""
    rng = np.random.default_rng(seed)
    cmd = np.zeros((n, 2))
    for start in range(60, n - 20, 90):
        cmd[start : start + 10, 0] = 1.0
    cmd[:, 1] = (np.arange(n) % 50 < 5).astype(float)
    v = 0.5 * np.sin(np.arange(n) / 8.0) + 0.4 * np.roll(cmd[:, 0], 1) + rng.normal(0, 0.01, n)
    return v, cmd


def test_features_pair_each_value_with_the_next_steps_commands() -> None:
    v = np.array([1.0, 2.0, 3.0])
    cmd = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    x = fc.features(v, cmd)
    assert x.shape == (3, 3)
    assert x[0].tolist() == [1.0, 0.0, 1.0]  # value_0 with commands of step 1
    assert x[2].tolist() == [3.0, 1.0, 1.0]  # last step repeats its own commands
    assert x.dtype == np.float32


def test_inputs_are_clipped_but_the_value_itself_is_not() -> None:
    x = fc.features(np.array([258.0, -258.0]), np.zeros((2, 1)))
    assert x[:, 0].tolist() == [fc.CLIP, -fc.CLIP]


def test_windows_cover_exactly_the_preceding_steps() -> None:
    x = np.arange(60, dtype=np.float32).reshape(60, 1)
    w = fc.windows(x, window=10)
    assert w.shape == (50, 10, 1)
    assert w[0, :, 0].tolist() == list(range(10))
    assert w[-1, -1, 0] == 58  # the last window predicts step 59
    assert fc.windows(x[:5], window=10).shape == (0, 10, 1)


def test_training_is_deterministic_and_learns() -> None:
    v, cmd = toy()
    a = fc.train_channel(v, cmd, seed=3, max_epochs=6)
    b = fc.train_channel(v, cmd, seed=3, max_epochs=6)
    assert a.val_loss == pytest.approx(b.val_loss)
    assert a.val_loss < float(np.var(v))  # beats predicting the mean
    with pytest.raises(ValueError, match="too short"):
        fc.train_channel(v[:60], cmd[:60], max_epochs=1)


def test_an_injected_anomaly_raises_the_smoothed_error() -> None:
    v, cmd = toy()
    res = fc.train_channel(v, cmd, seed=1, max_epochs=10)
    test_v, test_c = toy(seed=9)
    bad = test_v.copy()
    bad[300:330] += 1.5
    err_bad = fc.smoothed_errors(bad, fc.predict(res.model, bad, test_c))
    err_ok = fc.smoothed_errors(test_v, fc.predict(res.model, test_v, test_c))
    assert err_bad[300:340].max() > 3 * err_ok[300:340].max()
    assert err_ok[: fc.WINDOW].max() == pytest.approx(
        err_ok[: fc.WINDOW].min(), abs=0.05
    )  # no-prediction zone


def test_onnx_matches_pytorch_at_batch_sizes_one_and_many(tmp_path: Path) -> None:
    v, cmd = toy(300)
    res = fc.train_channel(v, cmd, seed=0, max_epochs=2)
    path = tmp_path / "m.onnx"
    fc.export_onnx(res.model, res.n_in, path)
    sess = fc.onnx_session(path)
    x = fc.windows(fc.features(v, cmd))[:33]
    import torch

    with torch.no_grad():
        ref = res.model(torch.from_numpy(x)).numpy()
    assert np.abs(fc.onnx_predict(sess, x) - ref).max() < 1e-4
    single = np.concatenate([fc.onnx_predict(sess, x[i : i + 1]) for i in range(5)])
    assert np.abs(single - ref[:5]).max() < 1e-4
