"""Single-instance rate limiting for the public analysis API."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from threading import Lock


@dataclass(frozen=True)
class RateLimitResult:
    """Decision returned by the in-memory limiter."""

    allowed: bool
    retry_after_seconds: int
    remaining: int


class InMemoryRateLimiter:
    """Simple fixed-window limiter keyed by caller subject."""

    def __init__(self, *, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._events: dict[str, deque[datetime]] = {}
        self._lock = Lock()

    def evaluate(self, subject: str, *, limit: int, now: datetime | None = None) -> RateLimitResult:
        """Evaluate and record a request for the supplied caller subject."""

        if limit <= 0:
            return RateLimitResult(allowed=False, retry_after_seconds=self.window_seconds, remaining=0)

        current_time = now or datetime.now(tz=UTC)
        window_start = current_time.timestamp() - self.window_seconds

        with self._lock:
            events = self._events.setdefault(subject, deque())
            while events and events[0].timestamp() <= window_start:
                events.popleft()

            if len(events) >= limit:
                retry_after_seconds = max(
                    1,
                    ceil(self.window_seconds - (current_time.timestamp() - events[0].timestamp())),
                )
                return RateLimitResult(
                    allowed=False,
                    retry_after_seconds=retry_after_seconds,
                    remaining=0,
                )

            events.append(current_time)
            remaining = max(0, limit - len(events))
            return RateLimitResult(
                allowed=True,
                retry_after_seconds=0,
                remaining=remaining,
            )
