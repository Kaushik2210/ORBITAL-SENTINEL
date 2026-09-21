"""Label incident windows from ground truth and summarize each run (evaluation side only).

Ground truth is used *only here*, to label training/evaluation data. The detectors and the
attribution features never see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinel_core.attribution.features import FeatureContext, extract
from sentinel_core.detection.engine import IncidentWindow
from sentinel_core.taxonomy import IncidentClass
from sentinel_core.timebase import STEP_SECONDS
from sentinel_sim.scenarios.spec import GroundTruth, ScenarioSpec

from .pipeline import RunResult

TAIL_STEPS = 120  # a window that starts this long after the truth interval still counts as it


@dataclass(slots=True)
class Sample:
    scenario_id: str
    variant: int
    label: str  # an IncidentClass value; windows away from the truth interval are 'nominal'
    truth_class: str
    subtype: str
    hard: bool
    features: dict[str, float]
    start_step: int
    end_step: int
    n_fired: int
    overlaps_truth: bool


@dataclass(slots=True)
class RunRecord:
    scenario_id: str
    variant: int
    truth: GroundTruth
    samples: list[Sample]
    steps: int
    detected: bool  # some fired output inside the truth interval (attack/fault scenarios)
    time_to_detect_steps: int | None
    false_windows: int  # incident windows that do not overlap the truth interval
    wall_s: float
    counts: dict[str, int] = field(default_factory=dict)
    windows: list[IncidentWindow] | None = None  # kept only when requested (variant 0 details)


def _overlaps(w: IncidentWindow, truth: GroundTruth) -> bool:
    if truth.start_step is None or truth.end_step is None:
        return False
    a, b = w.start / STEP_SECONDS, w.end / STEP_SECONDS
    return a <= truth.end_step + TAIL_STEPS and b >= truth.start_step


def make_record(
    spec: ScenarioSpec, variant: int, truth: GroundTruth, res: RunResult, keep_windows: bool = False
) -> RunRecord:
    ctx = FeatureContext(weather_covered=truth.weather_context != "unavailable")
    samples: list[Sample] = []
    for w in res.windows:
        ov = _overlaps(w, truth)
        label = truth.klass.value if ov else IncidentClass.NOMINAL.value
        samples.append(
            Sample(
                spec.id,
                variant,
                label,
                truth.klass.value,
                truth.subtype,
                truth.hard,
                extract(w, ctx),
                round(w.start / STEP_SECONDS),
                round(w.end / STEP_SECONDS),
                len(w.fired),
                ov,
            )
        )
    ttd: int | None = None
    if truth.start_step is not None and truth.end_step is not None:
        hits = [
            round(o.ts / STEP_SECONDS)
            for o in res.outputs
            if o.fired and truth.start_step <= o.ts / STEP_SECONDS <= truth.end_step + TAIL_STEPS
        ]
        ttd = min(hits) - truth.start_step if hits else None
    return RunRecord(
        spec.id, variant, truth, samples, res.steps, ttd is not None, ttd,
        sum(1 for s in samples if not s.overlaps_truth), res.wall_s,
        windows=res.windows if keep_windows else None,
    )  # fmt: skip
