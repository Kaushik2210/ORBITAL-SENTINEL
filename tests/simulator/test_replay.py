from __future__ import annotations

import asyncio
import time

import pytest

from sentinel_core.ingest import Tick
from sentinel_sim import pcoe
from sentinel_sim.mission import Mission, MissionConfig
from sentinel_sim.replay import ReplayEngine


def engine(speed: float = 0.0, max_steps: int | None = 20) -> ReplayEngine:
    m = Mission(
        MissionConfig(seed=1),
        battery=pcoe.parametric_battery(),
        wheel=pcoe.parametric_wheel(),
    )
    return ReplayEngine(m, speed=speed, max_steps=max_steps)


async def collect(e: ReplayEngine) -> list[Tick]:
    return [t async for t in e.stream()]


def test_unpaced_replay_emits_every_step_in_order_then_stops() -> None:
    ticks = asyncio.run(collect(engine(max_steps=25)))
    assert [t.k for t in ticks] == list(range(25))


def test_pacing_matches_requested_speed() -> None:
    e = engine(speed=6000.0, max_steps=15)  # 60 s / 6000 = 10 ms per step
    t0 = time.perf_counter()
    asyncio.run(collect(e))
    elapsed = time.perf_counter() - t0
    assert 0.12 < elapsed < 1.5  # 15 steps * 10 ms, with generous slack for slow CI


def test_pause_blocks_and_play_resumes_without_losing_or_repeating_steps() -> None:
    async def scenario() -> list[int]:
        e = engine(speed=60000.0, max_steps=30)  # 1 ms per step
        got: list[int] = []

        async def consume() -> None:
            async for t in e.stream():
                got.append(t.k)

        task = asyncio.create_task(consume())
        while len(got) < 5:
            await asyncio.sleep(0.001)
        e.pause()
        await asyncio.sleep(0.05)
        frozen = len(got)
        await asyncio.sleep(0.05)
        assert len(got) == frozen  # nothing advanced while paused
        assert not e.playing
        e.play()
        await task
        return got

    assert asyncio.run(scenario()) == list(range(30))


def test_seek_jumps_forward_and_back() -> None:
    async def scenario() -> list[int]:
        e = engine(speed=60000.0, max_steps=40)
        got: list[int] = []
        async for t in e.stream():
            got.append(t.k)
            if t.k == 5 and len(got) == 6:
                e.seek(30)
            if t.k == 32:
                e.seek(10)
            if t.k == 12 and 32 in got:
                break
        return got

    got = asyncio.run(scenario())
    assert got[:6] == [0, 1, 2, 3, 4, 5]
    assert got[6:9] == [30, 31, 32]
    assert got[9:12] == [10, 11, 12]


def test_seek_outside_range_is_rejected() -> None:
    e = engine(max_steps=10)
    with pytest.raises(ValueError, match="outside"):
        e.seek(11)
    with pytest.raises(ValueError, match="outside"):
        e.seek(-1)


def test_stop_ends_a_paused_stream() -> None:
    async def scenario() -> int:
        e = engine(speed=60000.0, max_steps=1000)
        got = 0

        async def consume() -> None:
            nonlocal got
            async for _ in e.stream():
                got += 1

        task = asyncio.create_task(consume())
        while got < 3:
            await asyncio.sleep(0.001)
        e.pause()
        await asyncio.sleep(0.02)
        e.stop()
        await asyncio.wait_for(task, timeout=2)
        return got

    assert asyncio.run(scenario()) < 1000


def test_speed_can_change_mid_stream() -> None:
    e = engine(speed=60.0)
    e.set_speed(0.0)
    assert e.speed == 0.0
    assert len(asyncio.run(collect(e))) == 20
