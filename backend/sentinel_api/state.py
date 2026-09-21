"""Application state: calibrated engine, attribution model, scenarios and live sessions."""

from __future__ import annotations

import asyncio
import contextlib
import copy
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from sentinel_core.attribution.model import AttributionModel
from sentinel_core.detection.engine import DetectionEngine
from sentinel_sim import pcoe
from sentinel_sim.pipeline import calibrated_engine
from sentinel_sim.replay import ReplayEngine
from sentinel_sim.scenarios.spec import GroundTruth, ScenarioSpec, load_all

from .db.engine import create_all, make_engine, make_session_factory
from .settings import Settings


@dataclass(slots=True)
class SessionRun:
    """One live or finished session and its pub/sub plumbing."""

    id: str
    kind: str
    scenario_id: str | None
    seed: int
    speed: float
    steps: int
    start: Any  # datetime
    status: str = "created"
    position: int = 0
    incidents: int = 0
    error: str | None = None
    labels: dict[str, list[list[int]]] | None = None
    truth: GroundTruth | None = None
    replay: ReplayEngine | None = None
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=300))

    def publish(self, msg: dict[str, Any]) -> None:
        """Fan a message out to subscribers; slow consumers drop the oldest sample, never block."""
        if msg["type"] != "samples":
            self.recent.append(msg)
        for q in self.subscribers:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):  # race with the consumer
                    q.get_nowait()
            q.put_nowait(msg)

    def subscribe(self, replay_recent: bool = True) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        if replay_recent:
            for m in self.recent:
                q.put_nowait(m)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db: AsyncEngine = make_engine(settings.database_url)
        self.factory: async_sessionmaker[AsyncSession] = make_session_factory(self.db)
        self.specs: dict[str, ScenarioSpec] = {s.id: s for s in load_all(settings.scenarios_dir)}
        self.model: AttributionModel | None = (
            AttributionModel.load(settings.attribution_model)
            if settings.attribution_model.is_file()
            else None
        )
        self.sessions: dict[str, SessionRun] = {}
        self._engine: DetectionEngine | None = None
        self._engine_lock = asyncio.Lock()
        self._battery: pcoe.BatteryTrajectory | None = None
        self._wheel: pcoe.WheelTrajectory | None = None

    async def start(self) -> None:
        await create_all(self.db)  # dev convenience; production uses Alembic migrations

    async def stop(self) -> None:
        for run in self.sessions.values():
            if run.task and not run.task.done():
                run.task.cancel()
        await asyncio.gather(
            *(r.task for r in self.sessions.values() if r.task), return_exceptions=True
        )
        await self.db.dispose()

    # -- detection engines ---------------------------------------------------------------
    def trajectories(self) -> tuple[pcoe.BatteryTrajectory, pcoe.WheelTrajectory]:
        if self._battery is None or self._wheel is None:
            root = self.settings.data_root
            self._battery, self._wheel = pcoe.load_battery(root), pcoe.load_wheel(root)
        return self._battery, self._wheel

    async def scenario_engine(self) -> DetectionEngine:
        """A private copy of the calibrated standard engine (calibrated once, off the loop)."""
        async with self._engine_lock:
            if self._engine is None:
                batt, wheel = await asyncio.to_thread(self.trajectories)
                self._engine = await asyncio.to_thread(
                    calibrated_engine,
                    self.settings.data_root,
                    batt,
                    wheel,
                    self.settings.calibration_steps,
                )
        assert self._engine is not None
        return copy.deepcopy(self._engine)

    def replay_engine(self, channels: list[str]) -> DetectionEngine:
        """Engine for real SMAP/MSL channels: L1 (calibrated on train), L2 if exported, packets."""
        from sentinel_core.detection.protocol import (
            AuthTagIntegrity,
            SequenceIntegrity,
            TimestampFreshness,
        )
        from sentinel_core.detection.statistical import StatisticalDetector
        from sentinel_core.events import TelemetryEvent
        from sentinel_core.timebase import STEP_SECONDS
        from sentinel_sim import smap_msl

        dets: list[Any] = [
            SequenceIntegrity(),
            TimestampFreshness(),
            AuthTagIntegrity(),
            StatisticalDetector(),
        ]
        if self.settings.l2_models_dir.is_dir() and any(self.settings.l2_models_dir.glob("*.onnx")):
            from sentinel_core.detection.l2 import ForecasterDetector

            dets.append(ForecasterDetector(self.settings.l2_models_dir))
        engine = DetectionEngine(dets)
        labels = smap_msl.load_labels(self.settings.data_root)
        calib = []
        for ch in channels:
            series = smap_msl.load_channel(self.settings.data_root, ch, labels)
            calib += [
                TelemetryEvent(k * STEP_SECONDS, ch, float(v), ts_rx=k * STEP_SECONDS)
                for k, v in enumerate(series.train)
            ]
        engine.calibrate(calib)
        return engine

    # -- helpers ---------------------------------------------------------------------------
    def running(self) -> int:
        return sum(
            1 for r in self.sessions.values() if r.status in ("created", "running", "paused")
        )
