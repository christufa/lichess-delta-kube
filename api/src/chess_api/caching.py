"""In-process response cache and per-IP rate limiter. Both live in one replica's memory;
with two replicas a visitor gets up to twice the rate, which is fine for a public dashboard.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from cachetools import TTLCache


@dataclass(frozen=True)
class Cached:
    body: bytes
    etag: str

    @classmethod
    def of(cls, body: bytes) -> Cached:
        return cls(body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"')


class ResponseCache:
    """TTL cache of serialized responses. Concurrent misses on one key run the query once."""

    def __init__(self, ttl_seconds: float, max_entries: int, timer: Callable[[], float] = time.monotonic):
        self._cache: TTLCache[str, Cached] = TTLCache(maxsize=max_entries, ttl=ttl_seconds, timer=timer)
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Lock] = {}

    def get_or_compute(self, key: str, compute: Callable[[], bytes]) -> tuple[Cached, bool]:
        """Returns (entry, hit)."""
        with self._lock:
            if (hit := self._cache.get(key)) is not None:
                return hit, True
            key_lock = self._inflight.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                if (hit := self._cache.get(key)) is not None:
                    return hit, True
            try:
                entry = Cached.of(compute())
                with self._lock:
                    self._cache[key] = entry
                return entry, False
            finally:
                with self._lock:
                    self._inflight.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class RateLimiter:
    """Token bucket per key: `burst` requests at once, refilled at `per_second`."""

    def __init__(self, per_second: float, burst: int, timer: Callable[[], float] = time.monotonic):
        self.per_second = per_second
        self.burst = burst
        self._timer = timer
        # A bucket that has been idle long enough to refill completely can be forgotten.
        idle = max(burst / per_second, 1.0) if per_second > 0 else 60.0
        self._buckets: TTLCache[str, tuple[float, float]] = TTLCache(maxsize=100_000, ttl=idle, timer=timer)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.per_second > 0 and self.burst > 0

    def take(self, key: str) -> float:
        """Takes one token. Returns 0 if allowed, else the seconds until a token is free."""
        now = self._timer()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.per_second)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / self.per_second
