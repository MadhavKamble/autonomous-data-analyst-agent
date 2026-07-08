"""Per-IP rate limiting on /ask.

This is a public demo with no user accounts (see README: no auth is a
deliberate scope decision, not an oversight). Without *some* limit, a single
visitor could drain the shared Groq daily quota (1,000 requests) for everyone
else. A per-IP fixed-window counter is the cheapest thing that actually
protects that shared resource; it is NOT a substitute for real per-user
quotas, which is exactly what changes in production (see README's
production-deployment section).

In-memory and single-process is fine here specifically because losing the
counters on a Render spin-down is harmless — unlike conversation state, a
rate-limit window resetting to zero after a cold start is the same as a new
window starting, not a correctness bug. Compare db/sessions.py, where losing
state on a restart would be a real bug and Postgres is used instead.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request

from app.config import Settings


class FixedWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self._window_seconds:
                hits.popleft()
            if len(hits) >= self._max_requests:
                return False
            hits.append(now)
            return True


_limiter: FixedWindowRateLimiter | None = None


def get_rate_limiter(settings: Settings) -> FixedWindowRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = FixedWindowRateLimiter(settings.rate_limit_per_minute, window_seconds=60.0)
    return _limiter


def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency: raises 429 once a client IP exceeds the window.

    request.client.host is what Render/most PaaS place in front of the app —
    fine for a single-instance demo; a real deployment behind a load balancer
    would read X-Forwarded-For instead (another production-deployment change).
    """
    settings: Settings = request.app.state.settings
    limiter = get_rate_limiter(settings)
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {settings.rate_limit_per_minute} requests/minute per IP.",
        )
