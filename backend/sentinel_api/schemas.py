"""Pydantic v2 request/response models for the public API (strict, no free-form input)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class Ready(BaseModel):
    ready: bool
    detection_engine: bool
    attribution_model: str | None
    l2_models: int
    real_data: bool


class ChannelOut(BaseModel):
    id: str
    family: str
    group: str
    unit: str | None
    synthetic: bool
    group_is_synthetic_grouping: bool


class ScenarioOut(BaseModel):
    """With ``reveal=false`` everything that would give the answer away is omitted."""

    id: str
    steps: int
    title: str | None = None
    narrative: str | None = None
    klass: str | None = Field(default=None, alias="class")
    subtype: str | None = None
    hard: bool | None = None
    confusable_with: list[str] | None = None
    sparta: list[str] | None = None
    attack: list[str] | None = None

    model_config = ConfigDict(populate_by_name=True)


class SessionCreate(Strict):
    kind: Literal["scenario", "replay"] = "scenario"
    scenario_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    variant: int = Field(default=0, ge=0, le=13)
    speed: float = Field(
        default=0.0, ge=0.0, le=100000.0, description="sim s per wall s; 0 = as fast as possible"
    )
    channels: list[str] = Field(default_factory=list, max_length=12)
    offset: int = Field(default=0, ge=0, le=100000)
    steps: int | None = Field(default=None, ge=10, le=20000)


class SessionOut(BaseModel):
    id: str
    kind: str
    status: str
    scenario_id: str | None
    seed: int
    speed: float
    position: int
    steps: int
    synthetic: bool
    start: datetime
    incidents: int
    error: str | None = None
    labels: dict[str, list[list[int]]] | None = None  # real replay only: labeled anomaly intervals


class Control(Strict):
    action: Literal["pause", "play", "speed"]
    speed: float | None = Field(default=None, ge=0.0, le=100000.0)


class TelemetryPoint(BaseModel):
    ts: datetime
    step: int
    value: float | None


class TelemetrySeries(BaseModel):
    channel: str
    synthetic: bool
    points: list[TelemetryPoint]


class IncidentOut(BaseModel):
    id: str
    session_id: str
    opened_at: datetime
    closed_at: datetime | None
    status: str
    verdict: str
    confidence: float
    severity: str
    posterior: dict[str, float]
    affected_channels: list[str]
    model_version: str
    synthetic: bool


class ContributionOut(BaseModel):
    feature: str
    value: float
    contribution: float


class EvidenceOut(BaseModel):
    incident: IncidentOut
    features: dict[str, float]
    evidence_for: list[ContributionOut]
    evidence_against: list[ContributionOut]
    runner_up: str | None
    detector_outputs: list[dict[str, Any]]


class LoginRequest(Strict):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a token *kind* label, not a secret
    role: str
    expires_in_seconds: int


class MeOut(BaseModel):
    email: str
    role: str


class AuditLogOut(BaseModel):
    id: int
    ts: datetime
    actor: str
    action: str
    target: str
    detail: dict[str, Any]


class AuditVerifyOut(BaseModel):
    ok: bool
    first_bad_row: int | None


class ReportOut(BaseModel):
    verdict: str
    confidence: float
    summary: str
    key_evidence: list[str]
    reasoning: list[str]
    recommended_action: str
    caveats: list[str]


class InvestigateOut(BaseModel):
    incident_id: str
    mode: Literal["llm", "offline"]
    model: str | None
    report: ReportOut
    markdown: str
    trace: list[dict[str, Any]]
    generated_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


class GroundTruthOut(BaseModel):
    scenario_id: str
    klass: str = Field(alias="class")
    subtype: str
    start_step: int | None
    end_step: int | None
    channels: list[str]
    sparta: list[str]
    attack: list[str]
    confusable_with: list[str]
    weather_context: str
    data_sources: dict[str, str]

    model_config = ConfigDict(populate_by_name=True)
