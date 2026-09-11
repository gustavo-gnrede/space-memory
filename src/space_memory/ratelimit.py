from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter keyed by an arbitrary string.

    Each key tracks a deque of hit timestamps within ``window_seconds``. When
    the deque reaches ``limit`` entries, further hits are rejected with the
    remaining time until the oldest hit expires.

    The number of tracked keys is bounded so a long-running server cannot leak
    memory from an unbounded set of client identities: when the map grows past
    ``max_keys``, stale keys are pruned first and, if still full, the map is
    cleared (a coarse, safe fallback).
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10_000) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.window = float(window_seconds)
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)``.

        ``retry_after_seconds`` is meaningful only when ``allowed`` is False.
        """
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                if len(self._hits) >= self.max_keys:
                    self._prune(now)
                    if len(self._hits) >= self.max_keys:
                        self._hits.clear()
                dq = deque()
                self._hits[key] = dq
            while dq and now - dq[0] >= self.window:
                dq.popleft()
            if len(dq) >= self.limit:
                retry_after = self.window - (now - dq[0])
                return False, max(0.0, retry_after)
            dq.append(now)
            return True, 0.0

    def _prune(self, now: float) -> None:
        for key in list(self._hits):
            dq = self._hits[key]
            while dq and now - dq[0] >= self.window:
                dq.popleft()
            if not dq:
                del self._hits[key]
