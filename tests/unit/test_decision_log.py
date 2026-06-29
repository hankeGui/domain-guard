"""Unit tests for the decision log."""

from __future__ import annotations

from domain_guard.context import GuardResult
from domain_guard.decision_log import DecisionLog


def _result(passed=True, layer="rule", conf=0.9):
    return GuardResult(passed=passed, matched_layer=layer, confidence=conf,
                       reason="x", latency_ms=1.0)


def test_records_and_replays():
    log = DecisionLog(max_entries=100)
    log.record("g", "hi", _result(passed=True), cache_hit=False)
    log.record("g", "bye", _result(passed=False, layer="rule"), cache_hit=False)

    items = log.recent(limit=10)
    assert len(items) == 2
    # Newest first
    assert items[0]["message"] == "bye"
    assert items[1]["message"] == "hi"


def test_bounded_size():
    log = DecisionLog(max_entries=3)
    for i in range(5):
        log.record("g", f"m{i}", _result(), cache_hit=False)
    items = log.recent(limit=10)
    assert len(items) == 3
    assert items[0]["message"] == "m4"  # newest


def test_filter_only_blocked():
    log = DecisionLog()
    log.record("g", "ok", _result(passed=True), cache_hit=False)
    log.record("g", "no", _result(passed=False), cache_hit=False)
    blocked = log.recent(only_blocked=True)
    assert len(blocked) == 1
    assert blocked[0]["message"] == "no"


def test_filter_by_guard():
    log = DecisionLog()
    log.record("a", "x", _result(), cache_hit=False)
    log.record("b", "y", _result(), cache_hit=False)
    assert len(log.recent(guard="a")) == 1


def test_summary():
    log = DecisionLog()
    for _ in range(3):
        log.record("a", "x", _result(passed=True, layer="rule"), False)
    for _ in range(2):
        log.record("b", "y", _result(passed=False, layer="embedding"), False)

    s = log.summary()
    assert s["total"] == 5
    assert s["passed"] == 3
    assert s["blocked"] == 2
    assert s["pass_rate"] == 0.6
    assert s["by_layer"] == {"rule": 3, "embedding": 2}
    assert s["by_guard"] == {"a": 3, "b": 2}


def test_empty_summary():
    log = DecisionLog()
    s = log.summary()
    assert s["total"] == 0
