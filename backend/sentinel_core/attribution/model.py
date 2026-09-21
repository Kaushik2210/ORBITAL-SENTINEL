"""Explainable attribution: multinomial logistic regression with exact additive contributions.

``logit_k = b_k + sum_j w_kj * x~_j`` where ``x~`` is the standardized feature vector. The term
``w_kj * x~_j`` is the *exact* contribution of feature j to class k (no SHAP approximation), so
"top evidence for/against" is the model itself, and a decision is fully reproducible from the
stored feature vector plus the model version (ADR 0006).

Abstention: the verdict is ``needs_human`` when the top posterior is below ``tau`` or the gap to
the runner-up is below ``delta``. Both are tuned on validation data, not chosen by hand.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from sentinel_core.taxonomy import IncidentClass, Verdict

CLASSES: tuple[str, ...] = tuple(c.value for c in IncidentClass)


@dataclass(frozen=True, slots=True)
class Contribution:
    feature: str
    value: float  # the raw feature value
    contribution: float  # exact logit-difference contribution (favoring the predicted class if > 0)


@dataclass(frozen=True, slots=True)
class Attribution:
    posterior: dict[str, float]
    verdict: Verdict
    top_class: str
    runner_up: str
    confidence: float
    margin: float
    evidence_for: tuple[Contribution, ...]
    evidence_against: tuple[Contribution, ...]
    features: dict[str, float]
    model_version: str


def _softmax(z: NDArray[np.float64]) -> NDArray[np.float64]:
    z = z - z.max()
    e = np.exp(z)
    return np.asarray(e / e.sum(), dtype=np.float64)


@dataclass(slots=True)
class AttributionModel:
    feature_names: tuple[str, ...]
    classes: tuple[str, ...]
    mean: NDArray[np.float64]
    scale: NDArray[np.float64]
    coef: NDArray[np.float64]  # (n_classes, n_features), in standardized space
    intercept: NDArray[np.float64]
    temperature: float
    tau: float
    delta: float
    trained_on: dict[str, object]

    @property
    def version(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def _standardize(self, features: dict[str, float]) -> NDArray[np.float64]:
        x = np.array([features[n] for n in self.feature_names], dtype=np.float64)
        return (x - self.mean) / self.scale

    def posterior(self, features: dict[str, float]) -> dict[str, float]:
        z = self.coef @ self._standardize(features) + self.intercept
        p = _softmax(z / self.temperature)
        return dict(zip(self.classes, (float(v) for v in p), strict=True))

    def predict(self, features: dict[str, float], top_k: int = 4) -> Attribution:
        xs = self._standardize(features)
        z = self.coef @ xs + self.intercept
        p = _softmax(z / self.temperature)
        order = np.argsort(-p)
        top, second = int(order[0]), int(order[1])
        contrib = (self.coef[top] - self.coef[second]) * xs  # exact, per feature
        idx = np.argsort(-contrib)

        def make(i: int) -> Contribution:
            name = self.feature_names[i]
            return Contribution(name, float(features[name]), float(contrib[i]))

        support = tuple(make(i) for i in idx[:top_k] if contrib[i] > 0)
        against = tuple(make(i) for i in idx[::-1][:top_k] if contrib[i] < 0)
        confidence, margin = float(p[top]), float(p[top] - p[second])
        abstain = confidence < self.tau or margin < self.delta
        return Attribution(
            posterior=dict(zip(self.classes, (float(v) for v in p), strict=True)),
            verdict=Verdict.NEEDS_HUMAN if abstain else Verdict(self.classes[top]),
            top_class=self.classes[top],
            runner_up=self.classes[second],
            confidence=confidence,
            margin=margin,
            evidence_for=support,
            evidence_against=against,
            features=dict(features),
            model_version=self.version,
        )

    # ---- persistence ------------------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "feature_names": list(self.feature_names),
            "classes": list(self.classes),
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "coef": self.coef.tolist(),
            "intercept": self.intercept.tolist(),
            "temperature": self.temperature,
            "tau": self.tau,
            "delta": self.delta,
            "trained_on": self.trained_on,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> AttributionModel:
        return cls(
            feature_names=tuple(d["feature_names"]),  # type: ignore[arg-type]
            classes=tuple(d["classes"]),  # type: ignore[arg-type]
            mean=np.array(d["mean"], dtype=np.float64),
            scale=np.array(d["scale"], dtype=np.float64),
            coef=np.array(d["coef"], dtype=np.float64),
            intercept=np.array(d["intercept"], dtype=np.float64),
            temperature=float(d["temperature"]),  # type: ignore[arg-type]
            tau=float(d["tau"]),  # type: ignore[arg-type]
            delta=float(d["delta"]),  # type: ignore[arg-type]
            trained_on=dict(d.get("trained_on", {})),  # type: ignore[call-overload]
        )

    @classmethod
    def load(cls, path: Path) -> AttributionModel:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
