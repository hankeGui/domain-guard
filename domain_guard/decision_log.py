"""Simple bounded ring buffer of recent guard decisions, used by the /admin UI."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Any

from .context import GuardResult


class DecisionLog:
    def __init__(self, max_entries: int = 200):
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = Lock()

    def record(self, guard_name: str, message: str, result: GuardResult,
               cache_hit: bool) -> None:
        with self._lock:
            self._buf.append({
                "ts": time.time(),
                "guard": guard_name,
                "message": message,
                "passed": result.passed,
                "layer": result.matched_layer,
                "confidence": round(result.confidence, 3),
                "reason": result.reason,
                "latency_ms": round(result.latency_ms, 2),
                "cache_hit": cache_hit,
            })

    def recent(self, limit: int = 50, guard: str | None = None,
               only_blocked: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        if guard:
            items = [x for x in items if x["guard"] == guard]
        if only_blocked:
            items = [x for x in items if not x["passed"]]
        return list(reversed(items[-limit:]))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._buf)
        if not items:
            return {"total": 0}
        total = len(items)
        passed = sum(1 for x in items if x["passed"])
        by_layer: dict[str, int] = {}
        by_guard: dict[str, int] = {}
        for x in items:
            by_layer[x["layer"] or "none"] = by_layer.get(x["layer"] or "none", 0) + 1
            by_guard[x["guard"]] = by_guard.get(x["guard"], 0) + 1
        avg_lat = sum(x["latency_ms"] for x in items) / total
        return {
            "total": total,
            "passed": passed,
            "blocked": total - passed,
            "pass_rate": passed / total,
            "by_layer": by_layer,
            "by_guard": by_guard,
            "avg_latency_ms": round(avg_lat, 3),
        }
