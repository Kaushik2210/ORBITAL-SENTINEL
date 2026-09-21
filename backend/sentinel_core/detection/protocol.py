"""L4: protocol and security detectors. They never look at telemetry *values*.

Attacker-controlled strings (command names, sources, log text) appear only in ``Evidence`` fields,
never inside ``explanation`` sentences, so downstream consumers (UI, investigation agent) receive
them strictly as data.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from sentinel_core.events import (
    AuthEvent,
    CommandEvent,
    Event,
    EventKind,
    LinkEvent,
    MalformedFrameEvent,
    PacketEvent,
)
from sentinel_core.packets import seq_delta
from sentinel_core.timebase import STEP_SECONDS

from .base import Detector, DetectorOutput, Evidence, Layer

Finding = tuple[float, str, Evidence]


def ev(
    name: str,
    observed: float | str | bool | None = None,
    expected: float | str | None = None,
    **kw: str,
) -> Evidence:
    """Short constructor for :class:`Evidence` (``unit=`` / ``note=`` via keywords)."""
    return Evidence(name=name, observed=observed, expected=expected, **kw)


def z_to_score(z: float, lo: float = 2.0, hi: float = 8.0) -> float:
    """Map a z-score to [0, 1]: 0 at ``lo`` sigma, 1 at ``hi`` sigma (0.5 at the midpoint)."""
    return min(1.0, max(0.0, (z - lo) / (hi - lo)))


@dataclass(frozen=True, slots=True)
class MissionKnowledge:
    """What the ground segment legitimately knows: opcodes, stations, operators, pass schedule."""

    opcodes: dict[int, str]
    stations: frozenset[str]
    operators: frozenset[str]
    in_contact: Callable[[float], bool]  # mission seconds -> is a ground pass active


# ------------------------------------------------------------------------------ packet layer


class SequenceIntegrity(Detector):
    """Sequence gaps, duplicates and regressions per APID (handles the 14-bit wrap).

    Tracks a high-water mark and a set of recently seen counters, so a replayed old frame arriving
    between live frames is a *regression/duplicate*, and the next live frame is *not* misread as a
    gap.
    """

    name = "l4.sequence_integrity"
    layer = Layer.L4
    consumes = frozenset({EventKind.PACKET})
    RECENT = 256

    def __init__(self) -> None:
        self._hwm: dict[int, int] = {}
        self._recent: dict[int, deque[int]] = {}

    def reset(self) -> None:
        self._hwm.clear()
        self._recent.clear()

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, PacketEvent):
            return []
        apid, seq = event.apid, event.seq_count
        recent = self._recent.setdefault(apid, deque(maxlen=self.RECENT))
        hwm = self._hwm.get(apid)
        if hwm is None:
            self._hwm[apid] = seq
            recent.append(seq)
            return []
        who = ev("apid", float(apid))
        if seq in recent:
            msg = "A sequence counter was seen again (duplicate or replayed frame)."
            return self._out(event.ts_rx, 0.9, msg, (ev("seq_count", float(seq)), who))
        delta = seq_delta(hwm, seq)
        recent.append(seq)
        if delta == 1:
            self._hwm[apid] = seq
            return []
        if delta > 1:
            self._hwm[apid] = seq
            lost = delta - 1
            return self._out(
                event.ts_rx,
                min(0.9, 0.4 + 0.1 * lost),
                "Packets are missing from the sequence (gap).",
                (ev("lost_packets", float(lost), 0.0), who),
            )
        msg = "The sequence counter went backwards (old frame re-sent)."
        return self._out(event.ts_rx, 0.95, msg, (ev("regression", float(delta), 1.0), who))


class TimestampFreshness(Detector):
    """Packet timestamp versus receive time: stale (replay/delay) or future-dated frames."""

    name = "l4.timestamp_freshness"
    layer = Layer.L4
    consumes = frozenset({EventKind.PACKET})

    def __init__(self, tolerance_s: float = STEP_SECONDS) -> None:
        self.tolerance_s = tolerance_s

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, PacketEvent):
            return []
        lag = event.ts_rx - event.ts_pkt
        if abs(lag) <= self.tolerance_s:
            return []
        kind = "stale" if lag > 0 else "future-dated"
        return self._out(
            event.ts_rx,
            min(1.0, 0.3 + abs(lag) / 600.0),
            f"The frame timestamp is {kind} relative to when it was received.",
            (ev("lag_s", lag, 0.0, unit="s"), ev("apid", float(event.apid))),
        )


class AuthTagIntegrity(Detector):
    """Failed authentication tags and structurally malformed frames."""

    name = "l4.auth_tag_integrity"
    layer = Layer.L4
    consumes = frozenset({EventKind.PACKET})

    def update(self, event: Event) -> list[DetectorOutput]:
        if isinstance(event, MalformedFrameEvent):
            return self._out(
                event.ts_rx,
                0.85,
                "A frame could not be parsed (malformed).",
                (ev("reason", event.reason[:120]), ev("size", float(event.size), unit="B")),
            )
        if isinstance(event, PacketEvent) and not event.auth_ok:
            return self._out(
                event.ts_rx,
                1.0,
                "A frame failed authentication (the tag does not match its content).",
                (ev("auth_ok", False, True), ev("apid", float(event.apid))),
            )
        return []


# ------------------------------------------------------------------------------ ground segment


class CommandPolicy(Detector):
    """Whitelist, authentication, contact-window, source and rate checks on telecommands."""

    name = "l4.command_policy"
    layer = Layer.L4
    consumes = frozenset({EventKind.COMMAND})

    def __init__(
        self, knowledge: MissionKnowledge, max_per_window: int = 5, window_s: float = 300.0
    ) -> None:
        self.k = knowledge
        self.max_per_window = max_per_window
        self.window_s = window_s
        self._recent: deque[float] = deque()

    def reset(self) -> None:
        self._recent.clear()

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, CommandEvent):
            return []
        while self._recent and event.ts - self._recent[0] > self.window_s:
            self._recent.popleft()
        self._recent.append(event.ts)

        found: list[Finding] = []
        if self.k.opcodes.get(event.opcode) != event.opcode_name:
            note = event.opcode_name[:64]
            found.append(
                (
                    1.0,
                    "an opcode that is not on the whitelist",
                    ev("opcode", float(event.opcode), note=note),
                )
            )
        if not event.auth_ok:
            found.append((1.0, "a command that failed authentication", ev("auth_ok", False, True)))
        if not (self.k.in_contact(event.ts) and event.in_contact_window):
            found.append(
                (
                    0.9,
                    "a command outside any ground contact window",
                    ev("in_contact_window", False, True),
                )
            )
        if event.source not in self.k.stations:
            found.append(
                (
                    0.85,
                    "a command from an unknown source",
                    ev("source", event.source[:64], note="not a known station"),
                )
            )
        if len(self._recent) > self.max_per_window:
            n = float(len(self._recent))
            found.append(
                (
                    0.7,
                    "an unusually high command rate",
                    ev("commands_in_window", n, float(self.max_per_window)),
                )
            )
        if not found:
            return []
        reasons = "; ".join(f[1] for f in found)
        return self._out(
            event.ts,
            max(f[0] for f in found),
            f"A telecommand was received: {reasons}.",
            tuple(f[2] for f in found),
        )


class AuthAnomaly(Detector):
    """Failed-authentication bursts, unknown sources and off-window logins."""

    name = "l4.auth_anomaly"
    layer = Layer.L4
    consumes = frozenset({EventKind.AUTH})

    def __init__(
        self, knowledge: MissionKnowledge, burst: int = 3, window_s: float = 600.0
    ) -> None:
        self.k = knowledge
        self.burst = burst
        self.window_s = window_s
        self._failures: deque[float] = deque()

    def reset(self) -> None:
        self._failures.clear()

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, AuthEvent):
            return []
        f = self._failures
        while f and event.ts - f[0] > self.window_s:
            f.popleft()
        if not event.success:
            f.append(event.ts)
        found: list[Finding] = []
        if len(f) >= self.burst:
            score = min(1.0, 0.6 + 0.1 * (len(f) - self.burst))
            found.append(
                (score, "a burst of failed logins", ev("failures_in_window", float(len(f)), 1.0))
            )
        if event.source not in self.k.operators:
            score = 0.8 if not event.success else 0.7
            found.append(
                (
                    score,
                    "a login attempt from an unknown account",
                    ev("source", event.source[:128], note="not in operator roster"),
                )
            )
        if not self.k.in_contact(event.ts):
            found.append(
                (
                    0.6,
                    "a login attempt outside a ground contact window",
                    ev("in_contact_window", False, True),
                )
            )
        if not found:
            return []
        return self._out(
            event.ts,
            max(x[0] for x in found),
            "Ground authentication looks abnormal: " + "; ".join(x[1] for x in found) + ".",
            tuple(x[2] for x in found),
        )


# ------------------------------------------------------------------------------ link layer


def _log10(x: float) -> float:
    return math.log10(max(x, 1e-15))


class LinkShift(Detector):
    """SNR drop, BER rise, latency and loss shifts versus the calibrated nominal link."""

    name = "l4.link_shift"
    layer = Layer.L4
    consumes = frozenset({EventKind.LINK})
    SIGN: ClassVar[dict[str, float]] = {"snr": -1.0, "logber": 1.0, "lat": 1.0, "loss": 1.0}
    FLOOR: ClassVar[dict[str, float]] = {"snr": 0.1, "logber": 0.1, "lat": 0.5, "loss": 0.01}

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = {k: [] for k in self.SIGN}
        self._stats: dict[str, tuple[float, float]] = {}

    @staticmethod
    def _observe(e: LinkEvent) -> dict[str, float]:
        return {"snr": e.snr_db, "logber": _log10(e.ber), "lat": e.latency_ms, "loss": e.loss_pct}

    def learn(self, event: Event) -> None:
        if isinstance(event, LinkEvent):
            for k, v in self._observe(event).items():
                self._samples[k].append(v)

    def freeze(self) -> None:
        for k, v in self._samples.items():
            if len(v) < 5:
                raise ValueError(f"LinkShift needs >= 5 calibration samples, got {len(v)}")
            self._stats[k] = (statistics.fmean(v), max(statistics.pstdev(v), self.FLOOR[k]))

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, LinkEvent):
            return []
        obs = self._observe(event)
        zs = {k: self.SIGN[k] * (obs[k] - self._stats[k][0]) / self._stats[k][1] for k in obs}
        score = max(z_to_score(z, 3.0, 9.0) for z in zs.values())
        worst = max(zs, key=lambda k: zs[k])
        return self._out(
            event.ts,
            score,
            "Link quality shifted away from its nominal behavior.",
            (*(ev(f"z_{k}", round(zs[k], 2), 0.0) for k in zs), ev("worst_metric", worst)),
        )


class RateAnomaly(Detector):
    """Frame-rate flood (DoS) and drop (dropout), plus queue depth, versus calibrated nominal."""

    name = "l4.rate_anomaly"
    layer = Layer.L4
    consumes = frozenset({EventKind.LINK})

    def __init__(self) -> None:
        self._rate: list[float] = []
        self._queue: list[float] = []
        self._base_rate = 0.0
        self._q_mean = 0.0
        self._q_std = 1.0

    def learn(self, event: Event) -> None:
        if isinstance(event, LinkEvent):
            self._rate.append(event.rx_pps)
            self._queue.append(float(event.queue_depth))

    def freeze(self) -> None:
        if len(self._rate) < 5:
            raise ValueError(f"RateAnomaly needs >= 5 calibration samples, got {len(self._rate)}")
        self._base_rate = statistics.median(self._rate)
        self._q_mean = statistics.fmean(self._queue)
        self._q_std = max(statistics.pstdev(self._queue), 1.0)

    def update(self, event: Event) -> list[DetectorOutput]:
        if not isinstance(event, LinkEvent) or self._base_rate <= 0:
            return []
        ratio = event.rx_pps / self._base_rate
        flood = min(1.0, max(0.0, (ratio - 1.5) / 4.0))
        drop = min(1.0, max(0.0, (0.8 - ratio) / 0.4))
        queue = z_to_score((event.queue_depth - self._q_mean) / self._q_std, 3.0, 9.0)
        kind = (
            "flood" if flood >= max(drop, queue) else "drop" if drop >= queue else "queue backlog"
        )
        return self._out(
            event.ts,
            max(flood, drop, queue),
            f"The received frame rate or queue depth is abnormal ({kind}).",
            (
                ev("rate_ratio", round(ratio, 2), 1.0),
                ev("rx_pps", event.rx_pps, self._base_rate, unit="frames/s"),
                ev("queue_depth", float(event.queue_depth), self._q_mean),
                ev("pattern", kind),
            ),
        )
