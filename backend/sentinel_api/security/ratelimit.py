"""A minimal in-memory, per-client-IP sliding-window rate limiter.

This is deliberately not Redis-backed: the project has no production deployment yet, and adding a
dependency for a single-process demo would be premature. It works correctly for one worker process
and is documented as **not** correct across multiple workers/replicas (each would keep its own
counters) — see docs/THREAT_MODEL.md.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

WINDOW_SECONDS = 60.0


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limit_per_minute: int) -> None:
        self.app = app
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.limit <= 0:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        now = time.monotonic()
        async with self._lock:
            hits = self._hits[ip]
            while hits and now - hits[0] > WINDOW_SECONDS:
                hits.popleft()
            over = len(hits) >= self.limit
            if not over:
                hits.append(now)
        if over:
            response = JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
