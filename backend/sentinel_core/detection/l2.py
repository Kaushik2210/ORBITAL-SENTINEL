"""Streaming L2 detector: ONNX forecaster + EWMA + rolling dynamic threshold.

For each channel with an exported model it predicts the value of the event that just arrived from
the previous ``WINDOW`` steps (using the commands that arrive with each value, as in training),
smooths the absolute error, and periodically re-derives the alarm threshold with the nonparametric
dynamic-threshold rule over a trailing window. It fires when the smoothed error exceeds the
threshold and stands clearly above the largest nominal error in that window (the paper's pruning
criterion).

Channels without an exported model are ignored. Deterministic for a given input order.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sentinel_core.events import Event, EventKind, TelemetryEvent

from .base import Detector, DetectorOutput, Layer, now
from .dynamic_threshold import MIN_DROP, choose_epsilon
from .protocol import ev

WINDOW = 48  # input steps per forecast (shared with training in sentinel_ml.forecaster)
CLIP = 20.0  # inputs are clipped to [-CLIP, CLIP]
SMOOTH_SPAN = 30  # EWMA span for the absolute error

TRAILING = 2100  # errors used to (re)derive the threshold
REFIT_EVERY = 30  # steps between threshold refreshes
ALPHA = 2.0 / (SMOOTH_SPAN + 1.0)


@dataclass(slots=True)
class _Channel:
    sess: Any  # onnxruntime.InferenceSession (lazy import: onnxruntime is optional)
    n_cmd: int
    history: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=WINDOW + 1))
    smoothed: float = 0.0
    errors: deque[float] = field(default_factory=lambda: deque(maxlen=TRAILING))
    eps: float | None = None
    since_refit: int = 0
    nominal_max: float = 0.0


def _bits(mask: int, n: int) -> NDArray[np.float32]:
    return np.array([(mask >> j) & 1 for j in range(n)], dtype=np.float32)


class ForecasterDetector(Detector):
    name = "l2.forecaster"
    layer = Layer.L2
    consumes = frozenset({EventKind.TELEMETRY})

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self._ch: dict[str, _Channel] = {}
        self._missing: set[str] = set()

    def reset(self) -> None:
        for c in self._ch.values():
            c.history.clear()
            c.errors.clear()
            c.smoothed, c.eps, c.since_refit, c.nominal_max = 0.0, None, 0, 0.0

    def _channel(self, name: str) -> _Channel | None:
        if name in self._ch:
            return self._ch[name]
        path = self.models_dir / f"{name}.onnx"
        if name in self._missing or not path.is_file():
            self._missing.add(name)
            return None
        import onnxruntime as ort  # lazy: only needed when a model actually exists

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # streaming inference is one tiny window at a time
        sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        n_in = int(sess.get_inputs()[0].shape[-1])
        self._ch[name] = _Channel(sess, n_in - 1)
        return self._ch[name]

    def last_error(self, channel: str) -> float:
        """Latest smoothed error (for tests and the API's evidence views)."""
        return self._ch[channel].smoothed

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, TelemetryEvent):
            return []
        c = self._channel(event.channel)
        if c is None or not np.isfinite(event.value):
            return []
        c.history.append((event.value, event.cmd_mask))
        if len(c.history) < WINDOW + 1:
            return []
        h = list(c.history)
        x = np.empty((1, WINDOW, 1 + c.n_cmd), dtype=np.float32)
        for i in range(WINDOW):
            x[0, i, 0] = np.clip(h[i][0], -CLIP, CLIP)
            x[0, i, 1:] = _bits(h[i + 1][1], c.n_cmd)  # commands that accompany the next value
        pred = float(c.sess.run(None, {"x": x})[0][0])
        err = abs(event.value - pred)
        c.smoothed = ALPHA * err + (1 - ALPHA) * c.smoothed if c.errors else err
        c.errors.append(c.smoothed)
        c.since_refit += 1
        if c.eps is None or c.since_refit >= REFIT_EVERY:
            arr = np.asarray(c.errors, dtype=np.float64)
            if len(arr) >= 60:
                c.eps = choose_epsilon(arr)
                below = arr[arr <= (c.eps if c.eps is not None else np.inf)]
                c.nominal_max = float(below.max()) if len(below) else 0.0
            c.since_refit = 0
        if c.eps is None or c.smoothed <= c.eps:
            return []
        if (c.smoothed - c.nominal_max) / c.smoothed < MIN_DROP:
            return []  # exceeds the threshold but not clearly above normal (pruned)
        return self._out(
            now(event),
            min(1.0, 0.5 * c.smoothed / c.eps),
            "The forecast error is far above what this channel normally produces.",
            (
                ev("smoothed_error", round(c.smoothed, 4), round(c.eps, 4)),
                ev("nominal_max_error", round(c.nominal_max, 4)),
                ev("predicted", round(pred, 4)),
                ev("observed", round(event.value, 4)),
            ),
            event.channel,
        )
