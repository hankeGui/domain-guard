"""Optional Prometheus metrics integration.

If `prometheus_client` is not installed, MetricsCollector is a no-op so the
core library has zero hard dependency on it.
"""

from __future__ import annotations

from typing import Any

from .context import GuardResult

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False
    Counter = Histogram = generate_latest = CONTENT_TYPE_LATEST = None  # type: ignore


class MetricsCollector:
    """Records guard outcomes as Prometheus metrics.

    Usage:
        metrics = MetricsCollector()
        metrics.attach(guard)             # registers as the guard's hook
        # ... requests flow ...
        metrics.render()                  # → bytes for /metrics endpoint
    """

    def __init__(self, namespace: str = "domain_guard"):
        self.available = _AVAILABLE
        if not self.available:
            return

        self._checks_total = Counter(
            f"{namespace}_checks_total",
            "Total guard checks performed.",
            ["guard", "verdict", "layer", "cache_hit"],
        )
        self._latency = Histogram(
            f"{namespace}_check_latency_ms",
            "End-to-end check latency in milliseconds.",
            ["guard", "verdict", "cache_hit"],
            buckets=(0.1, 0.5, 1, 5, 10, 50, 100, 250, 500, 1000, 5000),
        )

    def attach(self, guard) -> None:
        if not self.available:
            return
        guard.add_observer(self._on_result)

    def _on_result(self, guard_name: str, *args) -> None:
        # Accept both (guard, result, cache_hit) and (guard, message, result, cache_hit).
        if len(args) == 2:
            result, cache_hit = args
        elif len(args) == 3:
            _, result, cache_hit = args
        else:
            return
        if not self.available:
            return
        verdict = "pass" if result.passed else "block"
        ch = "true" if cache_hit else "false"
        self._checks_total.labels(
            guard=guard_name,
            verdict=verdict,
            layer=result.matched_layer or "none",
            cache_hit=ch,
        ).inc()
        self._latency.labels(guard=guard_name, verdict=verdict, cache_hit=ch).observe(
            result.latency_ms
        )

    def render(self) -> tuple[bytes, str]:
        if not self.available:
            return b"# prometheus_client not installed\n", "text/plain"
        return generate_latest(), CONTENT_TYPE_LATEST
