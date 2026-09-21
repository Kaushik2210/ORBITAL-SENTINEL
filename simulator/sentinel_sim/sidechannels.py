"""Seeded, deterministic synthetic side channels: commands, ground auth, link metrics.

Everything here is **synthetic** (ADR 0003) and generated from the documented statistical models
below. Ground stations, operators and opcodes are made-up names; nothing refers to a real system.

Models
* Contact: one pass per 90-step orbit, ``0.15 <= phase < 0.30`` (13-14 steps); stations rotate.
* Commands: inside a contact, each step emits a whitelisted command with p = 0.12.
* Auth: one operator login at each contact start; 2 % have a typo (fail, then retry next step).
* Link: SNR ~ N(14 dB, 0.6); BER = 10^-((SNR-2)/1.5), a monotone toy curve floored at 1e-12;
  latency ~ N(250 ms, 4); loss ~ |N(0.05 %, 0.03 %)|. ``rx_pps`` and ``queue_depth`` are filled
  in by the mission from the actual frame count.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sentinel_core.events import AuthEvent, CommandEvent, LinkEvent
from sentinel_core.timebase import STEP_SECONDS, rng_for

ORBIT_STEPS = 90
CONTACT_START, CONTACT_END = 0.15, 0.30

# Whitelisted telecommands (synthetic). Weights sum to 1.
OPCODES: dict[str, int] = {
    "NOOP": 0x01,
    "PAYLOAD_ON": 0x10,
    "PAYLOAD_OFF": 0x11,
    "WHEEL_BIAS": 0x20,
    "DUMP_MOMENTUM": 0x21,
    "SET_MODE": 0x30,
    "DOWNLINK_START": 0x40,
}
OPCODE_WEIGHTS = (0.30, 0.15, 0.15, 0.10, 0.05, 0.10, 0.15)
GROUND_STATIONS = ("GS-ALPHA", "GS-BRAVO", "GS-CHARLIE")
OPERATORS = ("op.kim", "op.rao", "op.novak", "op.silva")


def in_contact(k: int) -> bool:
    phase = (k % ORBIT_STEPS) / ORBIT_STEPS
    return CONTACT_START <= phase < CONTACT_END


def station_for(k: int) -> str:
    return GROUND_STATIONS[(k // ORBIT_STEPS) % len(GROUND_STATIONS)]


def contact_starts(k: int) -> bool:
    return in_contact(k) and not in_contact(k - 1)


def ber_from_snr(snr_db: float) -> float:
    return float(min(0.5, max(1e-12, 10.0 ** (-(snr_db - 2.0) / 1.5))))


@dataclass(frozen=True, slots=True)
class SideEvents:
    commands: tuple[CommandEvent, ...] = ()
    auths: tuple[AuthEvent, ...] = ()
    link: LinkEvent | None = None

    def with_link(self, link: LinkEvent) -> SideEvents:
        return replace(self, link=link)


class SideChannels:
    """Generates the nominal side channels one step at a time (RNG order matters: step in order)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.reset()

    def reset(self) -> None:
        self._cmd = rng_for(self.seed, "commands")
        self._auth = rng_for(self.seed, "auth")
        self._link = rng_for(self.seed, "link")
        self._retry_at: int | None = None

    def step(self, k: int) -> SideEvents:
        ts = k * STEP_SECONDS
        commands: list[CommandEvent] = []
        auths: list[AuthEvent] = []
        contact = in_contact(k)

        if contact and self._cmd.random() < 0.12:
            name = str(self._cmd.choice(list(OPCODES), p=OPCODE_WEIGHTS))
            commands.append(CommandEvent(ts, OPCODES[name], name, station_for(k), True, True))

        operator = str(self._auth.choice(OPERATORS))
        if contact_starts(k):
            typo = self._auth.random() < 0.02
            auths.append(AuthEvent(ts, operator, not typo))
            if typo:
                self._retry_at = k + 1
        elif self._retry_at == k:
            auths.append(AuthEvent(ts, operator, True))
            self._retry_at = None

        snr = 14.0 + float(self._link.normal(0, 0.6))
        link = LinkEvent(
            ts=ts,
            snr_db=snr,
            ber=ber_from_snr(snr),
            latency_ms=250.0 + float(self._link.normal(0, 4.0)),
            loss_pct=abs(float(self._link.normal(0.05, 0.03))),
            rx_pps=0.0,
            queue_depth=0,
        )
        return SideEvents(tuple(commands), tuple(auths), link)


def queue_depth(base: float, frames: int, nominal_frames: int) -> int:
    """Toy queue-depth model: base backlog plus one queued frame per 5 frames above nominal."""
    return max(0, round(base)) + max(0, (frames - nominal_frames) // 5)
