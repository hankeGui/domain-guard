"""Unit tests for the result cache (LRU)."""

from __future__ import annotations

import time

import pytest

from domain_guard import DomainGuard, GuardContext
from domain_guard.cache import LRUResultCache, make_cache_key


def test_lru_basic_hit_and_miss():
    c = LRUResultCache(max_size=10, ttl_seconds=60)
    assert c.get("k") is None
    # Forge a tiny result-like object using a real GuardResult
    from domain_guard.context import GuardResult
    r = GuardResult(passed=True, matched_layer="rule", confidence=0.9, reason="x")
    c.set("k", r)
    got = c.get("k")
    assert got is not None and got.passed is True
    stats = c.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1


def test_lru_eviction_oldest_first():
    c = LRUResultCache(max_size=2, ttl_seconds=60)
    from domain_guard.context import GuardResult
    for i in range(3):
        c.set(f"k{i}", GuardResult(passed=True))
    # k0 should have been evicted
    assert c.get("k0") is None
    assert c.get("k1") is not None
    assert c.get("k2") is not None


def test_lru_ttl_expiry():
    c = LRUResultCache(max_size=10, ttl_seconds=0.1)
    from domain_guard.context import GuardResult
    c.set("k", GuardResult(passed=True))
    time.sleep(0.2)
    assert c.get("k") is None


def test_cache_key_includes_state():
    ctx1 = GuardContext(state=None)
    ctx2 = GuardContext(state={"intent": "x", "stage": "collecting_slots"})
    k1 = make_cache_key("g", "hello", ctx1)
    k2 = make_cache_key("g", "hello", ctx2)
    assert k1 != k2


def test_cache_key_message_normalized():
    ctx = GuardContext()
    assert make_cache_key("g", "hi", ctx) == make_cache_key("g", "  hi  ", ctx)


def test_guard_uses_cache(guard):
    cache = LRUResultCache(max_size=16)
    guard.cache = cache

    r1 = guard.check("查产品A的forecast")
    r2 = guard.check("查产品A的forecast")
    assert r1.passed and r2.passed
    assert r2.debug.get("cache_hit") is True
    stats = cache.stats()
    assert stats["hits"] >= 1


def test_state_change_busts_cache(guard):
    cache = LRUResultCache(max_size=16)
    guard.cache = cache

    msg = "帮我看下数据"
    r1 = guard.check(msg, GuardContext())
    r2 = guard.check(msg, GuardContext(
        state={"intent": "forecast_management", "stage": "collecting_slots"}
    ))
    # Different state → different cache key → NOT a hit and a different verdict.
    assert r2.debug.get("cache_hit") is not True
    assert r1.matched_layer != r2.matched_layer
