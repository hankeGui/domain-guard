"""Rate limiting — token bucket, in-memory or Redis-backed.

Keep it intentionally small: two backends, one API, no extra dependencies
unless you opt into Redis. The sidecar wires it in front of /v1/check; you can
also drop it into any FastAPI app via the `rate_limit_dependency` helper.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_in: float          # seconds until the bucket is full again
    limit: int


class RateLimiter:
    """Pluggable interface — subclass for backends."""

    def take(self, key: str, cost: int = 1) -> RateLimitResult:
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        return {}


class TokenBucketLimiter(RateLimiter):
    """In-process token bucket. Thread-safe, no external state.

    Each key has its own bucket holding up to `capacity` tokens, refilling at
    `refill_per_sec`. take() returns allowed=False when the bucket is empty.
    """

    def __init__(self, capacity: int = 60, refill_per_sec: float = 1.0):
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_sec <= 0:
            raise ValueError("refill_per_sec must be > 0")
        self.capacity = capacity
        self.refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()
        self._allowed = 0
        self._rejected = 0

    def take(self, key: str, cost: int = 1) -> RateLimitResult:
        now = time.time()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            # Refill
            elapsed = max(0.0, now - last)
            tokens = min(self.capacity, tokens + elapsed * self.refill)
            allowed = tokens >= cost
            if allowed:
                tokens -= cost
                self._allowed += 1
            else:
                self._rejected += 1
            self._buckets[key] = (tokens, now)
            reset_in = max(0.0, (self.capacity - tokens) / self.refill)
            return RateLimitResult(
                allowed=allowed,
                remaining=int(tokens),
                reset_in=reset_in,
                limit=self.capacity,
            )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._allowed + self._rejected
            return {
                "backend": "memory",
                "capacity": self.capacity,
                "refill_per_sec": self.refill,
                "active_buckets": len(self._buckets),
                "allowed": self._allowed,
                "rejected": self._rejected,
                "rejection_rate": (self._rejected / total) if total else 0.0,
            }


class RedisRateLimiter(RateLimiter):
    """Fixed-window counter in Redis (simpler & cheaper than token bucket in
    redis-land — perfectly adequate for "N requests per minute per user").

    Uses INCR + EXPIRE; one round trip per call.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        limit: int = 60,
        window_seconds: int = 60,
        prefix: str = "guard_rl:",
    ):
        try:
            import redis  # type: ignore
        except ImportError as e:
            raise ImportError("pip install redis to use RedisRateLimiter") from e
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self.limit = limit
        self.window = window_seconds
        self.prefix = prefix
        self._allowed = 0
        self._rejected = 0
        self._lock = threading.Lock()

    def take(self, key: str, cost: int = 1) -> RateLimitResult:
        bucket_key = self.prefix + key
        pipe = self._client.pipeline()
        pipe.incrby(bucket_key, cost)
        pipe.expire(bucket_key, self.window, nx=True)  # only set TTL on creation
        pipe.ttl(bucket_key)
        count, _, ttl = pipe.execute()
        count = int(count)
        ttl_sec = max(0.0, float(ttl)) if ttl and ttl > 0 else float(self.window)
        allowed = count <= self.limit
        with self._lock:
            if allowed:
                self._allowed += 1
            else:
                self._rejected += 1
        return RateLimitResult(
            allowed=allowed,
            remaining=max(0, self.limit - count),
            reset_in=ttl_sec,
            limit=self.limit,
        )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._allowed + self._rejected
            return {
                "backend": "redis",
                "limit": self.limit,
                "window_seconds": self.window,
                "allowed": self._allowed,
                "rejected": self._rejected,
                "rejection_rate": (self._rejected / total) if total else 0.0,
            }
