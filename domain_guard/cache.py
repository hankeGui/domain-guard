"""Result cache: skip the pipeline for repeat messages.

Key includes guard name + normalized message + state-derived signature, since
the context_bypass layer can flip the verdict based on state.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import asdict
from threading import Lock
from typing import Any

from .context import GuardContext, GuardResult


# Only these state fields are referenced by ContextBypassLayer in our codebase
# today. If you add new bypass conditions, extend this list.
_STATE_FIELDS_FOR_SIG = ("intent", "stage")


def _state_signature(state: dict[str, Any] | None) -> str:
    if not state:
        return ""
    parts = []
    for k in _STATE_FIELDS_FOR_SIG:
        v = state.get(k)
        parts.append(f"{k}={v!r}")
    return "|".join(parts)


def make_cache_key(guard_name: str, message: str, ctx: GuardContext) -> str:
    normalized = message.strip()
    sig = _state_signature(ctx.state)
    blob = f"{guard_name}::{normalized}::{sig}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class ResultCache:
    """Pluggable interface — get/set GuardResult by key. Subclass for backends."""

    def get(self, key: str) -> GuardResult | None:
        raise NotImplementedError

    def set(self, key: str, result: GuardResult) -> None:
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        return {}


class LRUResultCache(ResultCache):
    """Thread-safe in-memory LRU with TTL."""

    def __init__(self, max_size: int = 1024, ttl_seconds: float = 3600.0):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[float, GuardResult]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> GuardResult | None:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            ts, value = entry
            if now - ts > self.ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, result: GuardResult) -> None:
        with self._lock:
            self._store[key] = (time.time(), result)
            self._store.move_to_end(key)
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "backend": "lru",
                "size": len(self._store),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (self._hits / total) if total else 0.0,
            }


class RedisResultCache(ResultCache):
    """Redis-backed cache. Stores results as JSON.

    Lazy-imports the `redis` package so it's only required if you use it.
    """

    def __init__(self, url: str = "redis://localhost:6379/0",
                 ttl_seconds: int = 3600, prefix: str = "domain_guard:"):
        try:
            import redis  # type: ignore
        except ImportError as e:
            raise ImportError("pip install redis to use RedisResultCache") from e
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self.ttl = ttl_seconds
        self.prefix = prefix
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> GuardResult | None:
        raw = self._client.get(self.prefix + key)
        if raw is None:
            self._misses += 1
            return None
        try:
            data = json.loads(raw)
            self._hits += 1
            return GuardResult(**data)
        except Exception:
            self._misses += 1
            return None

    def set(self, key: str, result: GuardResult) -> None:
        # Strip debug to keep payloads small in Redis.
        payload = asdict(result)
        payload.pop("debug", None)
        self._client.setex(self.prefix + key, self.ttl, json.dumps(payload, ensure_ascii=False))

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "prefix": self.prefix,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / total) if total else 0.0,
        }
