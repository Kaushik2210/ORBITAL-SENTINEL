"""Paced replay of a :class:`~sentinel_sim.mission.Mission` with play/pause/speed/seek control.

``speed`` is simulated seconds per wall-clock second (60 = one 60 s step per real second).
``speed <= 0`` means unpaced (as fast as the consumer accepts), which tests and evaluations use.
Control methods are safe to call from other tasks while :meth:`stream` is being consumed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sentinel_core.ingest import Tick
from sentinel_core.timebase import STEP_SECONDS

from .mission import Mission


class ReplayEngine:
    def __init__(self, mission: Mission, speed: float = 60.0, max_steps: int | None = None) -> None:
        self.mission = mission
        self.speed = speed
        self._limit = self._resolve_limit(max_steps)
        self._playing = asyncio.Event()
        self._playing.set()
        self._seek_to: int | None = None
        self._stopped = False

    def _resolve_limit(self, max_steps: int | None) -> int | None:
        avail = self.mission.steps_available
        if max_steps is None:
            return avail
        return max_steps if avail is None else min(max_steps, avail)

    # -- control ---------------------------------------------------------------------
    def play(self) -> None:
        self._playing.set()

    def pause(self) -> None:
        self._playing.clear()

    def set_speed(self, speed: float) -> None:
        self.speed = speed

    def seek(self, k: int) -> None:
        """Jump so the next emitted tick is step ``k`` (applies at the next loop iteration)."""
        if k < 0 or (self._limit is not None and k > self._limit):
            raise ValueError(f"seek target {k} outside [0, {self._limit}]")
        self._seek_to = k

    def stop(self) -> None:
        self._stopped = True
        self._playing.set()  # wake a paused stream so it can exit

    @property
    def playing(self) -> bool:
        return self._playing.is_set()

    @property
    def position(self) -> int:
        return self.mission.position

    # -- stream ----------------------------------------------------------------------
    async def stream(self) -> AsyncIterator[Tick]:
        while not self._stopped:
            await self._playing.wait()
            if self._stopped:
                break
            if self._seek_to is not None:
                target, self._seek_to = self._seek_to, None
                self.mission.seek(target)
            k = self.mission.position
            if self._limit is not None and k >= self._limit:
                break
            yield self.mission.tick(k)
            if self.speed > 0:
                await asyncio.sleep(STEP_SECONDS / self.speed)
            else:
                await asyncio.sleep(0)  # let control tasks run even when unpaced
