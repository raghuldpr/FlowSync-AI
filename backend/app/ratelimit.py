"""Tiny sliding-window rate limiter for public POST endpoints (clock is injectable, so tests can advance time)."""
import threading
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock):
        self._clock, self._lock, self._hits = clock, threading.Lock(), defaultdict(deque)

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 5000:                      # bound memory under a flood of distinct keys
                for k in [k for k, v in self._hits.items() if not v][:1000]:
                    self._hits.pop(k, None)
            return True
