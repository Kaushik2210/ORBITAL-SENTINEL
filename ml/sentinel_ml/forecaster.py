"""Telemanom-style LSTM forecaster (L2): train per channel, export to ONNX, score residuals.

Each step's input is ``[x_t, c_(t+1)]``: the telemetry value and the *next* step's multi-hot command
vector (commands are scheduled, so they are known ahead of the value they influence). The model
predicts ``x_(t+1)``. Prediction errors are EWMA-smoothed and thresholded with Hundman et al.'s
nonparametric dynamic thresholding (``sentinel_core.detection.dynamic_threshold``).

Design notes tied to the measured data (docs/DATASETS.md):
* command columns are multi-hot, so they enter as a binary vector, never an index;
* inputs are clipped to [-CLIP, CLIP] (channel M-6 reaches 258 in test); targets are not clipped,
  so a wild value still produces a large error;
* channels with a constant training signal train to predict that constant: any departure is an
  error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort
import torch
from numpy.typing import NDArray
from torch import nn

from sentinel_core.detection.dynamic_threshold import ewma
from sentinel_core.detection.l2 import CLIP, SMOOTH_SPAN, WINDOW

HIDDEN = 80
LAYERS = 2

__all__ = [
    "CLIP", "HIDDEN", "LAYERS", "SMOOTH_SPAN", "WINDOW", "Forecaster", "TrainResult",
    "export_onnx", "features", "onnx_predict", "onnx_session", "predict", "smoothed_errors",
    "train_channel", "windows",
]  # fmt: skip


class Forecaster(nn.Module):
    """2-layer LSTM (80 units) with a linear head on the last hidden state."""

    def __init__(self, n_in: int, hidden: int = HIDDEN, layers: int = LAYERS) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            n_in, hidden, layers, batch_first=True, dropout=0.3 if layers > 1 else 0
        )
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return cast(torch.Tensor, self.head(out[:, -1]).squeeze(-1))


def features(values: NDArray[np.float64], cmd: NDArray[np.float64]) -> NDArray[np.float32]:
    """Per-step inputs ``[x_t, c_(t+1)]`` (the last step repeats its own commands)."""
    nxt = np.vstack([cmd[1:], cmd[-1:]]) if len(cmd) else cmd
    x = np.concatenate([np.clip(values, -CLIP, CLIP)[:, None], nxt], axis=1)
    return x.astype(np.float32)


def windows(x: NDArray[np.float32], window: int = WINDOW) -> NDArray[np.float32]:
    """All length-``window`` input windows: ``out[i]`` covers steps ``i .. i+window-1``."""
    n = len(x) - window
    if n <= 0:
        return np.empty((0, window, x.shape[1]), dtype=np.float32)
    idx = np.arange(window)[None, :] + np.arange(n)[:, None]
    return cast(NDArray[np.float32], x[idx])


@dataclass(slots=True)
class TrainResult:
    model: Forecaster
    n_in: int
    train_loss: float
    val_loss: float
    epochs: int


def train_channel(
    values: NDArray[np.float64],
    cmd: NDArray[np.float64],
    seed: int = 0,
    max_epochs: int = 15,  # Telemanom uses 35; capped for CPU time (documented deviation)
    patience: int = 3,
) -> TrainResult:
    """Fit one channel on its nominal training array. Deterministic for a given seed."""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    x = features(values, cmd)
    xw = windows(x)
    y = np.clip(values, -CLIP, CLIP)[WINDOW:].astype(np.float32)
    n = len(y)
    if n < 20:
        raise ValueError(f"only {n} training windows: too short to train a forecaster")
    order = rng.permutation(n)
    n_val = max(4, n // 5)
    val_idx, tr_idx = order[:n_val], order[n_val:]
    model = Forecaster(x.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    xt, yt = torch.from_numpy(xw), torch.from_numpy(y)
    best, best_state, bad, epochs = float("inf"), None, 0, 0
    for epochs in range(1, max_epochs + 1):  # noqa: B007 - the last value is reported
        model.train()
        perm = torch.from_numpy(rng.permutation(tr_idx))
        for i in range(0, len(perm), 64):
            b = perm[i : i + 64]
            opt.zero_grad()
            loss_fn(model(xt[b]), yt[b]).backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            val = float(loss_fn(model(xt[val_idx]), yt[val_idx]))
        if val < best - 1e-6:
            best, best_state, bad = val, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= patience:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        train_loss = float(loss_fn(model(xt[tr_idx]), yt[tr_idx]))
    return TrainResult(model, x.shape[1], train_loss, best, epochs)


def predict(
    model: Forecaster, values: NDArray[np.float64], cmd: NDArray[np.float64]
) -> NDArray[np.float64]:
    """One-step predictions aligned to ``values`` (NaN for the first WINDOW steps)."""
    x = features(values, cmd)
    xw = windows(x)
    out = np.full(len(values), np.nan)
    if len(xw) == 0:
        return out
    model.eval()
    with torch.no_grad():
        preds = np.concatenate(
            [model(torch.from_numpy(xw[i : i + 512])).numpy() for i in range(0, len(xw), 512)]
        )
    out[WINDOW:] = preds
    return out


def smoothed_errors(values: NDArray[np.float64], preds: NDArray[np.float64]) -> NDArray[np.float64]:
    """EWMA-smoothed absolute prediction error; steps without a prediction get error 0."""
    err = np.where(np.isnan(preds), 0.0, np.abs(values - np.nan_to_num(preds)))
    return ewma(err, SMOOTH_SPAN)


# ------------------------------------------------------------------------------------------ ONNX


def export_onnx(model: Forecaster, n_in: int, path: Path) -> None:
    """Export with a dynamic batch axis; verified against PyTorch in the tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.zeros(1, WINDOW, n_in)
    torch.onnx.export(
        model,
        (dummy,),
        str(path),
        input_names=["x"],
        output_names=["y"],
        dynamic_axes={"x": {0: "batch"}, "y": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )


def onnx_session(path: Path) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1  # streaming inference is one tiny window at a time
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def onnx_predict(sess: ort.InferenceSession, xw: NDArray[np.float32]) -> NDArray[np.float32]:
    out: NDArray[np.float32] = sess.run(None, {"x": xw})[0]
    return out
