"""Simulation time conventions shared by the simulator, ingestion and detectors."""

from __future__ import annotations

import zlib
from datetime import UTC, datetime, timedelta

import numpy as np

# One simulation step is 60 s of mission time. This is a SIMULATION CONVENTION: the SMAP/MSL
# dataset documentation available to us does not state the real sample cadence.
STEP_SECONDS = 60.0

# Packet timestamps count seconds (uint32) from this epoch.
MISSION_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

# Default session start when a scenario does not need to align with a real DONKI event.
DEFAULT_START = datetime(2024, 5, 14, 0, 0, tzinfo=UTC)


def step_to_ts(k: int) -> float:
    """Mission time (seconds since session start) of step ``k``."""
    return k * STEP_SECONDS


def wall_us(start: datetime, ts: float) -> int:
    """Packet timestamp (microseconds since :data:`MISSION_EPOCH`) for mission time ``ts``."""
    return round(((start - MISSION_EPOCH) + timedelta(seconds=ts)).total_seconds() * 1e6)


def rng_for(seed: int, stream: str) -> np.random.Generator:
    """Independent, reproducible RNG per (seed, named stream).

    Naming streams (instead of drawing everything from one generator) keeps a scenario's noise
    unchanged when an unrelated generator is added or reordered.
    """
    return np.random.default_rng([seed, zlib.crc32(stream.encode())])
