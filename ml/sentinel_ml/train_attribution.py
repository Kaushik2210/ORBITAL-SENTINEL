"""Train the attribution model (multinomial logistic regression) and tune abstention.

Model selection uses only the validation split. The test split is touched exactly once, by
``evaluate.py``. sklearn is used for fitting only; inference is pure NumPy in ``sentinel_core``.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression

from sentinel_core.attribution.features import FEATURES
from sentinel_core.attribution.model import CLASSES, AttributionModel

from .dataset import RunRecord, Sample
from .splits import split_of

C_GRID = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
TARGET_ANSWERED_ACCURACY = 0.90


def load_records(path: Path) -> list[RunRecord]:
    with path.open("rb") as fh:
        records: list[RunRecord] = pickle.load(fh)  # noqa: S301 - our own generated artifact
    return records


def samples_by_split(records: list[RunRecord]) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {"train": [], "val": [], "test": []}
    for r in records:
        out[split_of(r.variant)].extend(r.samples)
    return out


def to_xy(samples: list[Sample]) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    x = np.array([[s.features[n] for n in FEATURES] for s in samples], dtype=np.float64)
    y = np.array([CLASSES.index(s.label) for s in samples], dtype=np.int64)
    return x, y


def _softmax_rows(z: NDArray[np.float64]) -> NDArray[np.float64]:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return np.asarray(e / e.sum(axis=1, keepdims=True), dtype=np.float64)


def _nll(logits: NDArray[np.float64], y: NDArray[np.int64], t: float) -> float:
    p = _softmax_rows(logits / t)
    return float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, None)).mean())


@dataclass(slots=True)
class Fit:
    model: AttributionModel
    c: float
    val_nll: float


def _fit_lr(
    x: NDArray[np.float64], y: NDArray[np.int64], c: float
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale < 1e-9, 1.0, scale)
    lr = LogisticRegression(C=c, class_weight="balanced", max_iter=5000)
    lr.fit((x - mean) / scale, y)
    coef = np.zeros((len(CLASSES), x.shape[1]))
    intercept = np.full(
        len(CLASSES), -20.0
    )  # a class never seen in training is effectively impossible
    if len(lr.classes_) == 2:  # sklearn stores a single row: logit(c1) - logit(c0) = w.x + b
        c0, c1 = (int(k) for k in lr.classes_)
        coef[c1], coef[c0] = lr.coef_[0] / 2, -lr.coef_[0] / 2
        intercept[c1], intercept[c0] = lr.intercept_[0] / 2, -lr.intercept_[0] / 2
    else:
        for row, cls in enumerate(lr.classes_):
            coef[int(cls)] = lr.coef_[row]
            intercept[int(cls)] = lr.intercept_[row]
    return mean, scale, coef, intercept


def fit_model(train: list[Sample], val: list[Sample]) -> Fit:
    x, y = to_xy(train)
    xv, yv = to_xy(val)
    best: Fit | None = None
    for c in C_GRID:
        mean, scale, coef, intercept = _fit_lr(x, y, c)
        logits_v = ((xv - mean) / scale) @ coef.T + intercept
        res = minimize_scalar(
            lambda t, lg=logits_v: _nll(lg, yv, t), bounds=(0.3, 5.0), method="bounded"
        )
        temp = float(res.x)
        nll = _nll(logits_v, yv, temp)
        model = AttributionModel(
            tuple(FEATURES), CLASSES, mean, scale, coef, intercept, temp, 0.0, 0.0,
            {"c": c, "n_train": len(train), "n_val": len(val)},
        )  # fmt: skip
        if best is None or nll < best.val_nll:
            best = Fit(model, c, nll)
    assert best is not None
    tune_abstention(best.model, val)
    return best


def tune_abstention(model: AttributionModel, val: list[Sample]) -> None:
    """Pick (tau, delta) maximizing coverage subject to answered accuracy on validation data."""
    posts = [model.posterior(s.features) for s in val]
    top = [max(p, key=lambda k: p[k]) for p in posts]
    conf = np.array([p[t] for p, t in zip(posts, top, strict=True)])
    second = np.array([sorted(p.values())[-2] for p in posts])
    margin = conf - second
    correct = np.array([t == s.label for t, s in zip(top, val, strict=True)])
    best = (-1.0, 0.0, 0.0)  # coverage, tau, delta
    for tau in np.linspace(0.3, 0.95, 27):
        for delta in np.linspace(0.0, 0.8, 17):
            answered = (conf >= tau) & (margin >= delta)
            if answered.sum() == 0:
                continue
            acc = float(correct[answered].mean())
            cov = float(answered.mean())
            if acc >= TARGET_ANSWERED_ACCURACY and cov > best[0]:
                best = (cov, float(tau), float(delta))
    if (
        best[0] < 0
    ):  # no setting reaches the target: fall back to the most accurate coverage >= 50 %
        for tau in np.linspace(0.3, 0.95, 27):
            for delta in np.linspace(0.0, 0.8, 17):
                answered = (conf >= tau) & (margin >= delta)
                if answered.mean() >= 0.5 and answered.sum():
                    acc = float(correct[answered].mean())
                    if acc > best[0]:
                        best = (acc, float(tau), float(delta))
    model.tau, model.delta = best[1], best[2]
    model.trained_on["abstention_target_accuracy"] = TARGET_ANSWERED_ACCURACY
