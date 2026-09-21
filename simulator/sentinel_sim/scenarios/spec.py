"""Scenario schema (YAML -> validated pydantic models) and ground truth.

Validation is strict on purpose: unknown effect types, missing parameters, windows outside the run,
a keyed attack without ``has_key`` and unverified SPARTA/ATT&CK ids are all rejected at load time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sentinel_core.taxonomy import IncidentClass

from . import tags
from .effects import REGISTRY, REQUIRED

# Measured from the cached DONKI data (docs/DATASETS.md, EVALUATION.md): no M/X flare, SEP or
# geomagnetic storm between 2024-05-24 20:25 and 2024-05-27 07:08 UTC.
QUIET_START = datetime(2024, 5, 25, 0, 0, tzinfo=UTC)
DEFAULT_STEPS = 1800
WARMUP_STEPS = 150  # detector inhibit window (start-up transient)


class EffectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    start: int
    end: int
    params: dict[str, Any] = Field(default_factory=dict)


class AttackerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_key: bool = False
    position: Literal["none", "sensor", "link", "ground"] = "none"


class AgingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # [start_step, end_step, progress_from, progress_to]
    battery: tuple[int, int, float, float] | None = None
    wheel: tuple[int, int, float, float] | None = None


class WeatherSpec(BaseModel):
    """Align the session with a real DONKI event: its onset lands on ``align_step``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["FLR", "SEP", "GST"]
    date_from: str
    date_to: str
    align_step: int
    min_flux: float | None = None  # flares only: minimum peak flux in W/m^2


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    title: str
    narrative: str
    klass: IncidentClass = Field(alias="class")
    subtype: str
    seed: int
    steps: int = DEFAULT_STEPS
    attacker: AttackerSpec = Field(default_factory=AttackerSpec)
    effects: list[EffectSpec] = Field(default_factory=list)
    aging: AgingSpec = Field(default_factory=AgingSpec)
    weather: WeatherSpec | None = None
    start: datetime | None = None  # None -> QUIET_START (or derived from ``weather``)
    confusable_with: list[str] = Field(default_factory=list)
    sparta: list[str] = Field(default_factory=list)
    attack: list[str] = Field(default_factory=list)
    hard: bool = False  # part of the confusable-pair evaluation

    @model_validator(mode="after")
    def _check(self) -> ScenarioSpec:
        tags.validate(self.sparta, self.attack)
        for i, e in enumerate(self.effects):
            if e.type not in REGISTRY:
                raise ValueError(f"{self.id}: effect {i} has unknown type {e.type!r}")
            missing = [p for p in REQUIRED.get(e.type, ()) if p not in e.params]
            if missing:
                raise ValueError(f"{self.id}: effect {i} ({e.type}) missing params {missing}")
            if not WARMUP_STEPS <= e.start < e.end <= self.steps:
                raise ValueError(
                    f"{self.id}: effect {i} window [{e.start},{e.end}) must lie in "
                    f"[{WARMUP_STEPS},{self.steps}]"
                )
            if e.type == "rewrite" and not self.attacker.has_key:
                raise ValueError(f"{self.id}: 'rewrite' needs attacker.has_key: true")
        if self.klass is IncidentClass.NOMINAL and (self.effects or self._has_aging()):
            raise ValueError(f"{self.id}: a nominal scenario cannot have effects")
        if self.klass is not IncidentClass.NOMINAL and not (self.effects or self._has_aging()):
            raise ValueError(f"{self.id}: a non-nominal scenario needs at least one effect")
        return self

    def _has_aging(self) -> bool:
        return self.aging.battery is not None or self.aging.wheel is not None


class GroundTruth(BaseModel):
    """What actually happened. Read only by the evaluator and the challenge/comparison UI."""

    scenario_id: str
    klass: IncidentClass
    subtype: str
    start_step: int | None
    end_step: int | None
    channels: list[str]
    sparta: list[str]
    attack: list[str]
    confusable_with: list[str]
    hard: bool
    data_sources: dict[str, str]  # e.g. {"battery": "pcoe"} vs "synthetic-parametric"
    weather_context: str  # "aligned" | "quiet-verified" | "unavailable"


def truth_from(
    spec: ScenarioSpec, data_sources: dict[str, str], weather_context: str
) -> GroundTruth:
    starts = [e.start for e in spec.effects]
    ends = [e.end for e in spec.effects]
    chans: list[str] = []
    for e in spec.effects:
        chans += list(e.params.get("targets", []))
    for plan, names in (
        (spec.aging.battery, ["batt_v_a", "batt_v_b", "batt_i"]),
        (spec.aging.wheel, ["rw1_vib_a", "rw1_vib_b", "rw1_temp"]),
    ):
        if plan:
            starts.append(plan[0])
            ends.append(plan[1])
            chans += names
    return GroundTruth(
        scenario_id=spec.id,
        klass=spec.klass,
        subtype=spec.subtype,
        start_step=min(starts) if starts else None,
        end_step=max(ends) if ends else None,
        channels=sorted(set(chans)),
        sparta=spec.sparta,
        attack=spec.attack,
        confusable_with=spec.confusable_with,
        hard=spec.hard,
        data_sources=data_sources,
        weather_context=weather_context,
    )


def load_scenario(path: Path) -> ScenarioSpec:
    return ScenarioSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all(directory: Path) -> list[ScenarioSpec]:
    specs = [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]
    ids = [s.id for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate scenario ids in {directory}")
    known = set(ids)
    for s in specs:
        unknown = [c for c in s.confusable_with if c not in known]
        if unknown:
            raise ValueError(f"{s.id}: confusable_with references unknown scenarios {unknown}")
    return specs


_SCALED = ("magnitude", "gain", "sigma", "noise_growth", "snr_drop_db", "loss_pct", "ber_decades")


def instantiate(spec: ScenarioSpec, variant: int) -> ScenarioSpec:
    """Seeded variant of a scenario: shifted timing, scaled magnitudes, independent noise.

    Variant 0 is the scenario exactly as written. Variants exist so attribution is trained and
    tested on many instances of each family rather than one hand-tuned example.
    """
    if variant == 0:
        return spec
    rng = np.random.default_rng([spec.seed, variant])
    scale = float(rng.uniform(0.8, 1.25))
    starts = [e.start for e in spec.effects] + [
        p[0] for p in (spec.aging.battery, spec.aging.wheel) if p
    ]
    ends = [e.end for e in spec.effects] + [
        p[1] for p in (spec.aging.battery, spec.aging.wheel) if p
    ]
    lo = WARMUP_STEPS - min(starts) if starts else 0
    hi = spec.steps - max(ends) if ends else 0
    shift = int(np.clip(rng.integers(-60, 61), lo, hi)) if starts else 0

    effects = []
    for e in spec.effects:
        params = dict(e.params)
        for k in _SCALED:
            if k in params and isinstance(params[k], int | float):
                params[k] = params[k] * scale
        if "source_start" in params:
            params["source_start"] = params["source_start"] + shift
        if "steps" in params:
            params["steps"] = [t + shift for t in params["steps"]]
        effects.append(
            e.model_copy(update={"start": e.start + shift, "end": e.end + shift, "params": params})
        )
    aging = spec.aging.model_copy(
        update={
            k: (v[0] + shift, v[1] + shift, v[2], v[3]) if (v := getattr(spec.aging, k)) else None
            for k in ("battery", "wheel")
        }
    )
    weather = (
        spec.weather.model_copy(update={"align_step": spec.weather.align_step + shift})
        if spec.weather
        else None
    )
    return spec.model_copy(
        update={
            "seed": spec.seed + 1000 * variant,
            "effects": effects,
            "aging": aging,
            "weather": weather,
        }
    )
