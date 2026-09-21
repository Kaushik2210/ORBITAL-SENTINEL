"""Database schema (SQLAlchemy 2, dialect-neutral; ADR 0007).

Time-series tables (``HYPERTABLES``) use a ``ts`` TIMESTAMPTZ column that is part of every unique
key, which TimescaleDB requires. They become hypertables only in the PostgreSQL migration; on
SQLite they are ordinary tables. Every table that can hold non-measured data carries a
``synthetic`` flag.

Ground truth lives in its own table and is never joined into what detectors read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


# ---------------------------------------------------------------------------- catalog


class Session(Base):
    """One run of the mission (a replay or a scenario)."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # replay | scenario
    scenario_id: Mapped[str | None] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(Integer)
    start: Mapped[datetime] = mapped_column(TS)  # mission t=0 in real (UTC) time
    step_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    speed: Mapped[float] = mapped_column(Float, default=60.0)
    status: Mapped[str] = mapped_column(String(16), default="created")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean)  # True if ANY data in it is synthetic
    created_at: Mapped[datetime] = mapped_column(TS)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    family: Mapped[str] = mapped_column(String(16))  # smap | msl | unlabeled | eps | adcs
    subsystem_group: Mapped[str] = mapped_column(String(16))
    group_is_synthetic_grouping: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(16))  # smap | msl | unlabeled | synthetic
    unit: Mapped[str | None] = mapped_column(String(16))
    synthetic: Mapped[bool] = mapped_column(Boolean)


# ---------------------------------------------------------------------------- time series


class Telemetry(Base):
    """Decoded samples. There is deliberately no 'injected' flag: ground truth is separate."""

    __tablename__ = "telemetry"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, primary_key=True)  # receive time
    n: Mapped[int] = mapped_column(Integer, primary_key=True)  # per-session arrival index
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    ts_pkt: Mapped[datetime] = mapped_column(TS)  # packet-claimed time (stale under replay)
    value: Mapped[float] = mapped_column(Float)
    cmd_mask: Mapped[int] = mapped_column(BigInteger, default=0)
    auth_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    synthetic: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (Index("ix_telemetry_session_channel_ts", "session_id", "channel_id", "ts"),)


class PacketRow(Base):
    __tablename__ = "packets"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, primary_key=True)  # receive time
    n: Mapped[int] = mapped_column(Integer, primary_key=True)  # per-session arrival index
    apid: Mapped[int] = mapped_column(Integer)
    seq_count: Mapped[int] = mapped_column(Integer)
    ts_pkt: Mapped[datetime] = mapped_column(TS)  # time claimed by the packet itself
    kind: Mapped[str] = mapped_column(String(4))  # TM | TC
    auth_ok: Mapped[bool] = mapped_column(Boolean)
    size: Mapped[int] = mapped_column(Integer)
    synthetic: Mapped[bool] = mapped_column(Boolean)


class MalformedFrame(Base):
    __tablename__ = "malformed_frames"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))


class LinkMetric(Base):
    __tablename__ = "link_metrics"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, primary_key=True)
    snr_db: Mapped[float] = mapped_column(Float)
    ber: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    loss_pct: Mapped[float] = mapped_column(Float)
    rx_pps: Mapped[float] = mapped_column(Float)
    queue_depth: Mapped[int] = mapped_column(Integer)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)


class DetectorOutputRow(Base):
    __tablename__ = "detector_outputs"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS, primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    detector: Mapped[str] = mapped_column(String(48))
    layer: Mapped[str] = mapped_column(String(4))
    channel_id: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float)
    fired: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")


HYPERTABLES = ("telemetry", "packets", "malformed_frames", "link_metrics", "detector_outputs")

# ---------------------------------------------------------------------------- logs


class CommandLog(Base):
    __tablename__ = "command_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    ts: Mapped[datetime] = mapped_column(TS)
    opcode: Mapped[int] = mapped_column(Integer)
    opcode_name: Mapped[str] = mapped_column(String(64))  # attacker-controllable free text
    source: Mapped[str] = mapped_column(String(64))  # attacker-controllable free text
    auth_ok: Mapped[bool] = mapped_column(Boolean)
    in_contact_window: Mapped[bool] = mapped_column(Boolean)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_command_log_session_ts", "session_id", "ts"),)


class AuthEventRow(Base):
    __tablename__ = "auth_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    ts: Mapped[datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(128))  # attacker-controllable free text
    success: Mapped[bool] = mapped_column(Boolean)
    method: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_auth_events_session_ts", "session_id", "ts"),)


# ---------------------------------------------------------------------------- incidents


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    opened_at: Mapped[datetime] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed|acknowledged
    verdict: Mapped[str] = mapped_column(String(24))  # a Verdict value
    confidence: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(12))
    posterior: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # for exact recompute
    top_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    affected_channels: Mapped[list[Any]] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(32))
    synthetic: Mapped[bool] = mapped_column(Boolean)


class GroundTruth(Base):
    """Scenario labels. Read only by the evaluator and the simulator/challenge UI."""

    __tablename__ = "ground_truth"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64))
    klass: Mapped[str] = mapped_column("class", String(24))
    subtype: Mapped[str] = mapped_column(String(48))
    start_ts: Mapped[datetime] = mapped_column(TS)
    end_ts: Mapped[datetime] = mapped_column(TS)
    channels: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(TS)
    mode: Mapped[str] = mapped_column(String(8))  # llm | offline
    model: Mapped[str | None] = mapped_column(String(64))
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)


class InvestigationEvent(Base):
    __tablename__ = "investigation_events"
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(16))  # tool_call | tool_result | note | final
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


# ---------------------------------------------------------------------------- external + ops


class DonkiEventRow(Base):
    __tablename__ = "donki_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(4), index=True)
    event_time: Mapped[datetime] = mapped_column(TS, index=True)
    end_time: Mapped[datetime | None] = mapped_column(TS)
    magnitude: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(TS)


class EvalRun(Base):
    """Every metric shown anywhere points to one of these (seed, profile, commit)."""

    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    git_sha: Mapped[str] = mapped_column(String(40))
    seed: Mapped[int] = mapped_column(Integer)
    profile: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TS)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    synthetic: Mapped[bool] = mapped_column(Boolean)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    role: Mapped[str] = mapped_column(String(12))  # viewer | analyst | admin
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(TS)


class AuditLog(Base):
    """Append-only and hash-chained: each row commits to the previous row's hash."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS)
    actor: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
