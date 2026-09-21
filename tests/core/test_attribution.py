from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sentinel_core.attribution.features import FEATURES, FeatureContext, extract, vector
from sentinel_core.attribution.model import CLASSES, AttributionModel
from sentinel_core.detection.base import DetectorOutput, Evidence, Layer
from sentinel_core.detection.engine import IncidentWindow
from sentinel_core.taxonomy import Verdict


def out(
    det: str,
    layer: Layer,
    score: float = 0.9,
    ts: float = 600.0,
    ch: str | None = None,
    expl: str = "x",
    **evidence: object,
) -> DetectorOutput:
    return DetectorOutput(
        detector=det,
        layer=layer,
        ts=ts,
        channel=ch,
        score=score,
        fired=score >= 0.5,
        explanation=expl,
        evidence=tuple(Evidence(name=k, observed=v) for k, v in evidence.items()),
    )


def window(outputs: list[DetectorOutput]) -> IncidentWindow:
    return IncidentWindow(1, min(o.ts for o in outputs), max(o.ts for o in outputs), outputs)


CTX = FeatureContext(weather_covered=True)


# ------------------------------------------------------------------ features


def test_feature_vector_is_complete_finite_and_ordered() -> None:
    f = extract(
        window(
            [out("l4.sequence_integrity", Layer.L4, expl="The sequence counter went backwards")]
        ),
        CTX,
    )
    assert list(f) == list(FEATURES)
    assert all(np.isfinite(v) for v in f.values())
    assert vector(f) == [f[n] for n in FEATURES]


def test_replay_evidence_is_distinguished_from_gap_and_stale_data() -> None:
    replay = extract(
        window(
            [
                out(
                    "l4.sequence_integrity",
                    Layer.L4,
                    0.95,
                    expl="The sequence counter went backwards (old frame re-sent).",
                ),
                out("l4.timestamp_freshness", Layer.L4, 0.8, lag_s=1800.0),
            ]
        ),
        CTX,
    )
    assert replay["seq_regression"] == pytest.approx(0.95)
    assert replay["stale_timestamp"] == pytest.approx(0.8)
    assert replay["seq_gap"] == 0.0
    stuck = extract(window([out("l1.statistical.flatline", Layer.L1, 1.0, ch="batt_v_a")]), CTX)
    assert stuck["seq_regression"] == 0.0
    assert stuck["l1_flatline"] == 1.0


def test_tag_failure_and_malformed_frames_are_separate_features() -> None:
    tag = extract(
        window([out("l4.auth_tag_integrity", Layer.L4, 1.0, auth_ok=False, apid=1.0)]), CTX
    )
    mal = extract(
        window([out("l4.auth_tag_integrity", Layer.L4, 0.85, reason="too short", size=3.0)]), CTX
    )
    assert (tag["auth_tag_failed"], tag["malformed_frames"]) == (1.0, 0.0)
    assert (mal["auth_tag_failed"], mal["malformed_frames"]) == (0.0, 0.85)


def test_flood_versus_drop_pattern() -> None:
    flood = extract(window([out("l4.rate_anomaly", Layer.L4, 0.9, pattern="flood")]), CTX)
    drop = extract(window([out("l4.rate_anomaly", Layer.L4, 0.8, pattern="drop")]), CTX)
    assert (flood["rate_flood"], flood["rate_drop"]) == (0.9, 0.0)
    assert (drop["rate_flood"], drop["rate_drop"]) == (0.0, 0.8)


def test_redundancy_shape_features_and_weather_context() -> None:
    w = window(
        [
            out(
                "l3.redundant_sensors.battery_voltage",
                Layer.L3,
                0.9,
                sidedness=-0.8,
                jump_z=30.0,
                slope_z_per_step=0.01,
            ),
            out("l5.weather_overlap", Layer.L5, 0.85, event_kind="SEP"),
        ]
    )
    f = extract(w, CTX)
    assert f["l3_sidedness"] == pytest.approx(0.8)  # magnitude: which side does not matter here
    assert f["l3_jump"] == 1.0  # capped
    assert f["weather_particle"] == 1.0
    assert extract(w, FeatureContext(weather_covered=False))["weather_covered"] == 0.0


def test_only_fired_outputs_drive_evidence_features() -> None:
    f = extract(
        window(
            [
                out("l1.statistical.range", Layer.L1, 0.4, ch="x"),
                out("l4.command_policy", Layer.L4, 0.9),
            ]
        ),
        CTX,
    )
    assert f["l1_range"] == 0.0
    assert f["command_policy"] == 0.9


def test_glitch_like_versus_sustained_l1_activity() -> None:
    few = extract(
        window(
            [
                out("l1.statistical.zscore", Layer.L1, ts=600.0 + 60 * i, ch=f"c{i}")
                for i in range(3)
            ]
        ),
        CTX,
    )
    many = extract(
        window(
            [out("l1.statistical.zscore", Layer.L1, ts=600.0 + 60 * i, ch="c") for i in range(40)]
        ),
        CTX,
    )
    assert few["l1_spike_like"] == 1.0
    assert many["l1_spike_like"] == 0.0


# ------------------------------------------------------------------ model


def toy_model(tau: float = 0.0, delta: float = 0.0) -> AttributionModel:
    n = len(FEATURES)
    rng = np.random.default_rng(0)
    coef = rng.normal(0, 1.0, (len(CLASSES), n))
    return AttributionModel(
        tuple(FEATURES),
        CLASSES,
        np.zeros(n),
        np.ones(n),
        coef,
        rng.normal(0, 0.1, len(CLASSES)),
        1.0,
        tau,
        delta,
        {"note": "toy"},
    )


def feats(seed: int = 1) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    return {n: float(v) for n, v in zip(FEATURES, rng.random(len(FEATURES)), strict=True)}


def test_posterior_is_a_distribution_and_top_class_is_its_argmax() -> None:
    m, f = toy_model(), feats()
    a = m.predict(f)
    assert sum(a.posterior.values()) == pytest.approx(1.0)
    assert a.top_class == max(a.posterior, key=lambda k: a.posterior[k])
    assert a.confidence == pytest.approx(a.posterior[a.top_class])
    assert a.verdict.value == a.top_class


def test_contributions_are_exact_not_approximate() -> None:
    """logit_top - logit_second == sum(per-feature contributions) + intercept gap, exactly."""
    m, f = toy_model(), feats(3)
    a = m.predict(f, top_k=len(FEATURES))
    xs = (np.array([f[n] for n in FEATURES]) - m.mean) / m.scale
    z = m.coef @ xs + m.intercept
    i, j = CLASSES.index(a.top_class), CLASSES.index(a.runner_up)
    total = sum(c.contribution for c in (*a.evidence_for, *a.evidence_against))
    assert total + (m.intercept[i] - m.intercept[j]) == pytest.approx(z[i] - z[j])


def test_evidence_for_and_against_have_the_right_signs_and_order() -> None:
    a = toy_model().predict(feats(5))
    assert all(c.contribution > 0 for c in a.evidence_for)
    assert all(c.contribution < 0 for c in a.evidence_against)
    assert [c.contribution for c in a.evidence_for] == sorted(
        (c.contribution for c in a.evidence_for), reverse=True
    )


def test_abstention_on_low_confidence_or_small_margin() -> None:
    f = feats(2)
    base = toy_model().predict(f)
    assert toy_model(tau=base.confidence + 0.01).predict(f).verdict is Verdict.NEEDS_HUMAN
    assert toy_model(delta=base.margin + 0.01).predict(f).verdict is Verdict.NEEDS_HUMAN
    assert (
        toy_model(tau=base.confidence - 0.01, delta=base.margin - 0.01).predict(f).verdict
        is not Verdict.NEEDS_HUMAN
    )


def test_a_decision_is_reproducible_from_stored_features_and_survives_persistence(
    tmp_path: Path,
) -> None:
    m, f = toy_model(0.3, 0.1), feats(7)
    first = m.predict(f)
    path = tmp_path / "model.json"
    m.save(path)
    loaded = AttributionModel.load(path)
    again = loaded.predict(first.features)  # recomputed from the stored feature vector alone
    assert again.posterior == first.posterior
    assert again.verdict is first.verdict
    assert loaded.version == m.version == first.model_version


def test_model_version_changes_when_weights_change() -> None:
    a, b = toy_model(), toy_model()
    b.coef = b.coef + 1e-6
    assert a.version != b.version


def test_missing_feature_is_an_error_not_a_silent_zero() -> None:
    f = feats()
    del f["l1_range"]
    with pytest.raises(KeyError):
        toy_model().predict(f)
