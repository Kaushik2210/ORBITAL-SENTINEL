"""Held-out evaluation of detection + attribution. Every reported number comes from here.

The test split (variants 10-13) is used exactly once, for the final numbers. Everything is
computed from saved run records; nothing is typed in by hand.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel_core.attribution.features import FEATURES
from sentinel_core.attribution.model import CLASSES, AttributionModel
from sentinel_core.taxonomy import IncidentClass, Verdict

from .dataset import RunRecord, Sample
from .splits import TEST, TRAIN, VAL
from .train_attribution import Fit, _fit_lr, fit_model, load_records, samples_by_split, to_xy

NH = Verdict.NEEDS_HUMAN.value
LABELS = (*CLASSES, NH)

GROUPS: dict[str, tuple[str, ...]] = {
    "L4 protocol/security": (
        "seq_regression",
        "seq_gap",
        "stale_timestamp",
        "auth_tag_failed",
        "malformed_frames",
        "command_policy",
        "auth_anomaly",
        "link_shift",
        "rate_flood",
        "rate_drop",
    ),
    "L1 statistical": (
        "l1_range",
        "l1_zscore",
        "l1_level_shift",
        "l1_cusum",
        "l1_rate_of_change",
        "l1_flatline",
        "l1_variance",
        "l1_dropout",
        "l1_channels",
        "l1_spike_like",
    ),
    "L3 physics/redundancy": (
        "l3_redundancy",
        "l3_sidedness",
        "l3_jump",
        "l3_slope",
        "l3_power_balance",
    ),
    "L5 environment": (
        "simultaneous_channels",
        "weather_overlap",
        "weather_particle",
        "weather_covered",
    ),
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a proportion (honest for small n)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def prop(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [lo, hi]}


def verdict_of(model: AttributionModel, s: Sample) -> str:
    return model.predict(s.features).verdict.value


def rule_baseline(f: dict[str, float]) -> str:
    """A transparent hand-written classifier: the bar the learned model has to clear."""
    if any(f[k] >= 0.5 for k in GROUPS["L4 protocol/security"]):
        return IncidentClass.CYBERATTACK.value
    if f["weather_overlap"] > 0 or f["simultaneous_channels"] > 0:
        return IncidentClass.ENVIRONMENTAL.value
    if (
        f["l3_redundancy"] > 0
        or f["l1_flatline"] > 0
        or f["l1_dropout"] > 0
        or f["l1_variance"] > 0
    ):
        return IncidentClass.SENSOR_MALFUNCTION.value
    if any(f[k] > 0 for k in GROUPS["L1 statistical"][:5]) or f["l3_power_balance"] > 0:
        return IncidentClass.MECHANICAL_FAILURE.value
    return IncidentClass.NOMINAL.value


def confusion(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    m: dict[str, dict[str, int]] = {t: dict.fromkeys(LABELS, 0) for t in CLASSES}
    for truth, pred in pairs:
        m[truth][pred] += 1
    return m


def per_class(pairs: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in CLASSES:
        tp = sum(1 for t, p in pairs if t == c and p == c)
        fp = sum(1 for t, p in pairs if t != c and p == c)
        fn = sum(1 for t, p in pairs if t == c and p != c)  # abstaining on a class counts as a miss
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if tp + fn else None)
        out[c] = {
            "precision": prec,
            "recall": rec,
            "f1": f1,
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
        }
    return out


def summarize(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    n = len(pairs)
    answered = [(t, p) for t, p in pairs if p != NH]
    correct = sum(1 for t, p in answered if t == p)
    return {
        "n": n,
        "strict_accuracy": prop(sum(1 for t, p in pairs if t == p), n),
        "answered_accuracy": prop(correct, len(answered)),
        "coverage": prop(len(answered), n),
        "abstain_when_it_would_have_been_wrong": None,
        "confusion": confusion(pairs),
        "per_class": per_class(pairs),
    }


# ------------------------------------------------------------------------------------------ pieces


def window_metrics(model: AttributionModel, test: list[Sample]) -> dict[str, Any]:
    preds = [(s.label, verdict_of(model, s)) for s in test]
    out = summarize(preds)
    top = [(s.label, model.predict(s.features).top_class) for s in test]
    wrong_top = [(t, p, v) for (t, p), (_, v) in zip(top, preds, strict=True) if t != p]
    out["abstain_when_it_would_have_been_wrong"] = prop(
        sum(1 for _, _, v in wrong_top if v == NH), len(wrong_top)
    )
    out["forced_choice_accuracy"] = prop(
        sum(1 for t, p in top if t == p), len(top)
    )  # never abstains
    return out


def scenario_table(model: AttributionModel, records: list[RunRecord]) -> list[dict[str, Any]]:
    by: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        if r.variant in TEST:
            by[r.scenario_id].append(r)
    rows: list[dict[str, Any]] = []
    for sid, rs in sorted(by.items()):
        t = rs[0].truth
        verdicts = Counter(verdict_of(model, s) for r in rs for s in r.samples if s.overlaps_truth)
        ttd = [r.time_to_detect_steps for r in rs if r.time_to_detect_steps is not None]
        is_nominal = t.klass is IncidentClass.NOMINAL
        n_win = sum(verdicts.values())
        rows.append(
            {
                "scenario": sid,
                "truth": t.klass.value,
                "subtype": t.subtype,
                "hard": t.hard,
                "runs": len(rs),
                "detected": None if is_nominal else prop(sum(r.detected for r in rs), len(rs)),
                "median_time_to_detect_min": statistics.median(ttd) if ttd else None,
                "windows": n_win,
                "verdicts": dict(verdicts),
                "window_accuracy": (
                    None if is_nominal or not n_win else verdicts.get(t.klass.value, 0) / n_win
                ),
                "false_windows": sum(r.false_windows for r in rs),
            }
        )
    return rows


def hard_pairs(
    model: AttributionModel, test: list[Sample], records: list[RunRecord]
) -> list[dict[str, Any]]:
    truth_of = {r.scenario_id: r.truth for r in records}
    seen: set[frozenset[str]] = set()
    rows = []
    for sid, t in sorted(truth_of.items()):
        for partner in t.confusable_with:
            key = frozenset({sid, partner})
            if key in seen or partner not in truth_of:
                continue
            seen.add(key)
            res: dict[str, Any] = {
                "pair": [sid, partner],
                "classes": [t.klass.value, truth_of[partner].klass.value],
            }
            for name in (sid, partner):
                ss = [s for s in test if s.scenario_id == name and s.overlaps_truth]
                v = Counter(verdict_of(model, s) for s in ss)
                own = truth_of[name].klass.value
                other = truth_of[partner if name == sid else sid].klass.value
                res[name] = {
                    "windows": len(ss),
                    "correct": prop(v.get(own, 0), len(ss)),
                    "abstained": v.get(NH, 0),
                    "confused_as_partner_class": v.get(other, 0) if other != own else 0,
                }
            rows.append(res)
    return rows


def false_alarms(model: AttributionModel, records: list[RunRecord]) -> dict[str, Any]:
    test = [r for r in records if r.variant in TEST]
    nominal = [r for r in test if r.truth.klass is IncidentClass.NOMINAL]
    days = sum(r.steps * 60 / 86400 for r in nominal)
    wins = [s for r in nominal for s in r.samples]
    other = [
        s
        for r in test
        if r.truth.klass is not IncidentClass.NOMINAL
        for s in r.samples
        if not s.overlaps_truth
    ]
    return {
        "nominal_runs": len(nominal),
        "simulated_days": days,
        "false_incident_windows": len(wins),
        "false_windows_per_day": (len(wins) / days) if days else None,
        "false_windows_classified_nominal_by_attribution": prop(
            sum(1 for s in wins if verdict_of(model, s) == IncidentClass.NOMINAL.value), len(wins)
        ),
        "false_windows_that_reach_operators": sum(
            1 for s in wins if verdict_of(model, s) != IncidentClass.NOMINAL.value
        ),
        "windows_outside_the_fault_in_fault_runs": len(other),
    }


def ablations(train: list[Sample], val: list[Sample], test: list[Sample]) -> list[dict[str, Any]]:
    def masked(ss: list[Sample], drop: tuple[str, ...]) -> list[Sample]:
        out = []
        for s in ss:
            f = dict(s.features)
            for k in drop:
                f[k] = 0.0
            out.append(_copy(s, f))
        return out

    rows = []
    for name, drop in (("all layers", ()), *((f"without {g}", cols) for g, cols in GROUPS.items())):
        fit = fit_model(masked(train, drop), masked(val, drop))
        preds = [(s.label, verdict_of(fit.model, s)) for s in masked(test, drop)]
        forced = [(s.label, fit.model.predict(s.features).top_class) for s in masked(test, drop)]
        rows.append(
            {
                "config": name,
                "strict_accuracy": prop(sum(1 for t, p in preds if t == p), len(preds)),
                "forced_choice_accuracy": prop(sum(1 for t, p in forced if t == p), len(forced)),
            }
        )
    return rows


def _copy(s: Sample, features: dict[str, float]) -> Sample:
    return Sample(
        s.scenario_id, s.variant, s.label, s.truth_class, s.subtype, s.hard, features,
        s.start_step, s.end_step, s.n_fired, s.overlaps_truth,
    )  # fmt: skip


def leave_one_scenario_out(records: list[RunRecord], c: float) -> list[dict[str, Any]]:
    """Train without one scenario entirely; classify its held-out windows (a stress test)."""
    fit_pool = [s for r in records if r.variant in TRAIN | VAL for s in r.samples]
    test_pool = [s for r in records if r.variant in TEST for s in r.samples if s.overlaps_truth]
    rows = []
    for sid in sorted({s.scenario_id for s in test_pool}):
        tr = [s for s in fit_pool if s.scenario_id != sid]
        te = [s for s in test_pool if s.scenario_id == sid]
        if not te or not tr:
            continue
        x, y = to_xy(tr)
        mean, scale, coef, intercept = _fit_lr(x, y, c)
        xt, _ = to_xy(te)
        pred = (((xt - mean) / scale) @ coef.T + intercept).argmax(axis=1)
        got = Counter(CLASSES[i] for i in pred)
        truth = te[0].truth_class
        rows.append(
            {
                "scenario": sid, "truth": truth, "windows": len(te),
                "correct": prop(got.get(truth, 0), len(te)), "predicted": dict(got),
            }
        )  # fmt: skip
    return rows


def git_sha() -> str:
    """HEAD commit, plus +dirty if tracked code or scenarios differ (honest provenance)."""
    code = ["backend", "simulator", "ml", "scripts", "data/scenarios", "pyproject.toml"]

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - fixed argv
            ["git", *args],  # noqa: S607 - git on PATH
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        dirty = bool(git("status", "--porcelain", "--", *code))
        return git("rev-parse", "HEAD") + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_all(records: list[RunRecord]) -> tuple[dict[str, Any], Fit]:
    sp = samples_by_split(records)
    fit = fit_model(sp["train"], sp["val"])
    model = fit.model
    test = sp["test"]
    baseline_pairs = [(s.label, rule_baseline(s.features)) for s in test]
    majority = Counter(s.label for s in sp["train"]).most_common(1)[0][0]
    metrics: dict[str, Any] = {
        "meta": {
            "git_sha": git_sha(),
            "created_at": datetime.now(UTC).isoformat(),
            "profile": "full",
            "splits": {"train": sorted(TRAIN), "val": sorted(VAL), "test": sorted(TEST)},
            "n_runs": len(records),
            "n_windows": {k: len(v) for k, v in sp.items()},
            "model_version": model.version,
            "model": {
                "c": fit.c,
                "temperature": model.temperature,
                "tau": model.tau,
                "delta": model.delta,
            },
            "synthetic": True,
            "note": (
                "Train and test scenarios come from the same simulator: this measures "
                "separability inside the simulator, not accuracy on real spacecraft."
            ),
            "data_sources": sorted({v for r in records for v in r.truth.data_sources.values()}),
            "weather_context": dict(Counter(r.truth.weather_context for r in records)),
        },
        "windows": window_metrics(model, test),
        "baselines": {
            "majority_class": {
                "class": majority,
                "accuracy": prop(sum(1 for s in test if s.label == majority), len(test)),
            },
            "hand_rules": summarize(baseline_pairs),
        },
        "scenarios": scenario_table(model, records),
        "hard_pairs": hard_pairs(model, test, records),
        "false_alarms": false_alarms(model, records),
        "ablations": ablations(sp["train"], sp["val"], test),
        "leave_one_scenario_out": leave_one_scenario_out(records, fit.c),
        "feature_names": list(FEATURES),
    }
    return metrics, fit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", type=Path, default=Path("data/processed/records.pkl"))
    ap.add_argument("--out", type=Path, default=Path("ml/runs/attribution"))
    args = ap.parse_args()
    records = load_records(args.records)
    metrics, fit = run_all(records)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(
        json.dumps(metrics, indent=2, default=float), encoding="utf-8"
    )
    fit.model.save(args.out / "attribution_model.json")
    w = metrics["windows"]
    print(
        f"test windows: {w['n']}  strict acc {w['strict_accuracy']['rate']:.3f}  "
        f"answered acc {w['answered_accuracy']['rate']:.3f}  coverage {w['coverage']['rate']:.3f}"
    )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
