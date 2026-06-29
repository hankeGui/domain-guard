"""Unit tests for the token-bucket rate limiter."""

from __future__ import annotations

import time

import pytest

from domain_guard.ratelimit import TokenBucketLimiter


def test_within_budget_all_allowed():
    lim = TokenBucketLimiter(capacity=5, refill_per_sec=1.0)
    results = [lim.take("alice") for _ in range(5)]
    assert all(r.allowed for r in results)


def test_over_budget_blocked():
    lim = TokenBucketLimiter(capacity=3, refill_per_sec=1.0)
    for _ in range(3):
        assert lim.take("alice").allowed
    r = lim.take("alice")
    assert not r.allowed
    assert r.remaining == 0
    assert r.reset_in > 0


def test_separate_keys_are_independent():
    lim = TokenBucketLimiter(capacity=2, refill_per_sec=1.0)
    assert lim.take("alice").allowed
    assert lim.take("alice").allowed
    assert not lim.take("alice").allowed
    # bob still has full bucket
    assert lim.take("bob").allowed


def test_refill_restores_tokens():
    lim = TokenBucketLimiter(capacity=2, refill_per_sec=100.0)  # very fast refill
    assert lim.take("alice").allowed
    assert lim.take("alice").allowed
    assert not lim.take("alice").allowed
    time.sleep(0.05)  # ~5 tokens refilled at 100/sec, capped at capacity
    assert lim.take("alice").allowed


def test_invalid_construction():
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=0, refill_per_sec=1.0)
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity=1, refill_per_sec=0)


def test_stats_track_decisions():
    lim = TokenBucketLimiter(capacity=2, refill_per_sec=0.1)
    for _ in range(4):
        lim.take("alice")
    s = lim.stats()
    assert s["allowed"] == 2
    assert s["rejected"] == 2
    assert s["rejection_rate"] == 0.5
