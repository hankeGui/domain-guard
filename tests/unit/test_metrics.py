"""Unit tests for the optional Prometheus metrics collector."""

from __future__ import annotations

import pytest

from domain_guard import DomainGuard
from domain_guard.metrics import MetricsCollector


def test_metrics_unavailable_when_lib_missing(monkeypatch):
    # Force the import-failure branch to verify no-op behaviour.
    monkeypatch.setattr("domain_guard.metrics._AVAILABLE", False)
    m = MetricsCollector()
    # attach() must not crash even if prometheus_client isn't installed
    class FakeGuard:
        def add_observer(self, fn): self.fn = fn
    g = FakeGuard()
    m.attach(g)   # should silently do nothing
    payload, ct = m.render()
    assert b"not installed" in payload


def test_metrics_records(guard):
    pytest.importorskip("prometheus_client")
    # Use a unique namespace so we don't collide with other tests in the run.
    m = MetricsCollector(namespace="dg_test_records")
    m.attach(guard)

    guard.check("查产品A的forecast")
    guard.check("你是什么模型")

    payload, _ = m.render()
    text = payload.decode("utf-8")
    assert "dg_test_records_checks_total" in text
    assert 'verdict="pass"' in text
    assert 'verdict="block"' in text
