from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("sklearn")

from sentinel_core.attribution.features import FEATURES
from sentinel_core.attribution.model import CLASSES
from sentinel_ml.dataset import Sample
from sentinel_ml.evaluate import confusion, per_class, prop, rule_baseline, wilson
from sentinel_ml.splits import TEST, TRAIN, VAL, split_of
from sentinel_ml.train_attribution import fit_model, tune_abstention


def synth(n_per_class: int, seed: int, overlap: float = 0.0) -> list[Sample]:
    """Separable toy data: class k lights up feature k (plus optional label noise)."""
    rng = np.random.default_rng(seed)
    out: list[Sample] = []
    for k, cls in enumerate(CLASSES):
        for i in range(n_per_class):
            f = dict.fromkeys(FEATURES, 0.0)
            f[FEATURES[k]] = float(rng.uniform(0.6, 1.0))
            for name in FEATURES:
                f[name] += float(rng.normal(0, 0.05))
            label = cls if rng.random() >= overlap else CLASSES[(k + 1) % len(CLASSES)]
            out.append(Sample(f"s{k}", i, label, cls, "t", False, f, 0, 1, 1, True))
    return out


def test_splits_are_disjoint_and_cover_every_variant() -> None:
    assert TRAIN.isdisjoint(VAL)
    assert TRAIN.isdisjoint(TEST)
    assert VAL.isdisjoint(TEST)
    assert {split_of(v) for v in range(14)} == {"train", "val", "test"}
    with pytest.raises(ValueError, match="no split"):
        split_of(99)


def test_fit_recovers_separable_classes_and_never_predicts_an_unseen_class() -> None:
    fit = fit_model(synth(40, 0), synth(15, 1))
    test = synth(20, 2)
    correct = sum(fit.model.predict(s.features).top_class == s.label for s in test)
    assert correct / len(test) > 0.95
    train_missing_env = [s for s in synth(40, 0) if s.label != "environmental"]
    fit2 = fit_model(train_missing_env, [s for s in synth(15, 1) if s.label != "environmental"])
    unseen = fit2.model.posterior(synth(1, 3)[2].features)  # an "environmental"-shaped input
    assert unseen["environmental"] < 1e-6


def test_abstention_is_tuned_on_validation_and_meets_the_target_when_possible() -> None:
    fit = fit_model(synth(40, 0, overlap=0.15), synth(30, 1, overlap=0.15))
    val = synth(30, 1, overlap=0.15)
    tune_abstention(fit.model, val)
    answered = [
        s
        for s in val
        if (a := fit.model.predict(s.features)).confidence >= fit.model.tau
        and a.margin >= fit.model.delta
    ]
    acc = sum(fit.model.predict(s.features).top_class == s.label for s in answered) / len(answered)
    assert acc >= 0.85


def test_temperature_is_fit_and_positive() -> None:
    fit = fit_model(synth(40, 0, overlap=0.2), synth(30, 1, overlap=0.2))
    assert 0.3 <= fit.model.temperature <= 5.0


def test_wilson_interval_behaves() -> None:
    lo, hi = wilson(0, 10)
    assert lo == 0.0
    assert 0.2 < hi < 0.4
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi
    assert wilson(0, 0) == (0.0, 1.0)
    p = prop(3, 4)
    assert p["rate"] == 0.75
    assert p["ci95"][0] < 0.75 < p["ci95"][1]


def test_confusion_and_per_class_metrics() -> None:
    pairs = (
        [("nominal", "nominal")] * 3
        + [("nominal", "cyberattack")]
        + [("cyberattack", "needs_human")] * 2
        + [("cyberattack", "cyberattack")]
    )
    m = confusion(pairs)
    assert m["nominal"]["nominal"] == 3
    assert m["nominal"]["cyberattack"] == 1
    assert m["cyberattack"]["needs_human"] == 2
    pc = per_class(pairs)
    assert pc["cyberattack"]["recall"] == pytest.approx(1 / 3)  # abstaining counts as a miss
    assert pc["cyberattack"]["precision"] == pytest.approx(0.5)


def test_rule_baseline_is_transparent() -> None:
    f = dict.fromkeys(FEATURES, 0.0)
    assert rule_baseline(f) == "nominal"
    assert rule_baseline({**f, "command_policy": 0.9}) == "cyberattack"
    assert rule_baseline({**f, "weather_overlap": 0.85}) == "environmental"
    assert rule_baseline({**f, "l1_flatline": 1.0}) == "sensor_malfunction"
    assert rule_baseline({**f, "l1_range": 0.6}) == "mechanical_failure"
