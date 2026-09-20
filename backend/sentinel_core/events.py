"""Events flowing from ingestion into the detection engine.

All ``ts`` values are mission time in seconds (simulated, monotone within a session unless a
scenario deliberately replays old frames). ``synthetic`` marks data that is not a real measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .packets import PacketType


class EventKind(StrEnum):
    TELEMETRY = "telemetry"
    PACKET = "packet"
    COMMAND = "command"
    AUTH = "auth"
    LINK = "link"
    SPACE_WEATHER = "space_weather"


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One decoded sample. ``cmd_mask`` is a bitmask of active command indicators.

    SMAP/MSL command columns are multi-hot (Phase 1), so a bitmask, not an index.
    """

    ts: float
    channel: str
    value: float
    cmd_mask: int = 0
    synthetic: bool = False
    kind: EventKind = EventKind.TELEMETRY


@dataclass(frozen=True, slots=True)
class PacketEvent:
    """Header-level view of a received frame, independent of payload semantics."""

    ts_rx: float
    apid: int
    seq_count: int
    ts_pkt: float
    ptype: PacketType
    auth_ok: bool
    size: int
    synthetic: bool = True
    kind: EventKind = EventKind.PACKET


@dataclass(frozen=True, slots=True)
class MalformedFrameEvent:
    """Raw bytes that failed structural parsing (truncated, bad length, ...)."""

    ts_rx: float
    size: int
    reason: str
    synthetic: bool = True
    kind: EventKind = EventKind.PACKET


@dataclass(frozen=True, slots=True)
class CommandEvent:
    """A telecommand on the uplink. ``source`` and ``opcode_name`` are attacker-controllable."""

    ts: float
    opcode: int
    opcode_name: str
    source: str
    auth_ok: bool
    in_contact_window: bool
    synthetic: bool = True
    kind: EventKind = EventKind.COMMAND


@dataclass(frozen=True, slots=True)
class AuthEvent:
    """Ground-segment authentication attempt. ``source`` is attacker-controllable free text."""

    ts: float
    source: str
    success: bool
    method: str = "password"
    synthetic: bool = True
    kind: EventKind = EventKind.AUTH


@dataclass(frozen=True, slots=True)
class LinkEvent:
    """Periodic link-quality sample."""

    ts: float
    snr_db: float
    ber: float
    latency_ms: float
    loss_pct: float
    rx_pps: float
    queue_depth: int
    synthetic: bool = True
    kind: EventKind = EventKind.LINK


@dataclass(frozen=True, slots=True)
class WeatherEvent:
    """A DONKI space-weather event mapped to mission time. Real data (cached)."""

    event_kind: str  # FLR | CME | GST | SEP | IPS
    begin_ts: float
    end_ts: float
    magnitude: float | None = None
    synthetic: bool = False
    kind: EventKind = EventKind.SPACE_WEATHER


Event = (
    TelemetryEvent
    | PacketEvent
    | MalformedFrameEvent
    | CommandEvent
    | AuthEvent
    | LinkEvent
    | WeatherEvent
)
