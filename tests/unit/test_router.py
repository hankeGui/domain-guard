"""Unit tests for the multi-guard router."""

from __future__ import annotations

import pytest

from domain_guard import GuardContext
from domain_guard.router import GuardRouter


def test_routes_to_first_pass(guard, hr_guard):
    r = GuardRouter([guard, hr_guard])
    # Forecast message → forecast guard
    res = r.route("查产品A的forecast", GuardContext())
    assert res.passed
    assert res.matched_guard == "forecast-agent-guard"


def test_routes_to_correct_agent(guard, hr_guard):
    r = GuardRouter([guard, hr_guard])
    res = r.route("我要请假", GuardContext())
    assert res.passed
    assert res.matched_guard == "hr-agent-guard"


def test_all_block_returns_no_match(guard, hr_guard):
    r = GuardRouter([guard, hr_guard])
    res = r.route("你是什么模型", GuardContext())
    assert not res.passed
    assert res.matched_guard is None
    assert len(res.alternatives) == 2   # both guards explained their block


def test_sticky_session_keeps_routing(guard, hr_guard):
    r = GuardRouter([guard, hr_guard], sticky=True)
    res1 = r.route("查产品A的forecast", GuardContext(session_id="s1"))
    assert res1.matched_guard == "forecast-agent-guard"
    # Now a message that "could be HR" — sticky should keep it on forecast.
    res2 = r.route("请填一下", GuardContext(
        session_id="s1",
        state={"intent": "forecast_management", "stage": "collecting_slots"},
    ))
    assert res2.matched_guard == "forecast-agent-guard"
    assert res2.sticky_hit is True


def test_sticky_drops_when_blocked(guard, hr_guard):
    r = GuardRouter([guard, hr_guard], sticky=True)
    r.route("查产品A的forecast", GuardContext(session_id="s1"))
    # Off-topic — even with sticky, blocking on the previous guard should
    # drop the sticky binding so future on-topic messages can re-route.
    res = r.route("你是什么模型", GuardContext(session_id="s1"))
    assert res.matched_guard is None


def test_clear_sticky(guard, hr_guard):
    r = GuardRouter([guard, hr_guard], sticky=True)
    r.route("查产品A的forecast", GuardContext(session_id="s1"))
    r.clear_sticky("s1")
    # Without sticky, an HR message should be allowed to land on HR guard.
    res = r.route("我要请假", GuardContext(session_id="s1"))
    assert res.matched_guard == "hr-agent-guard"


def test_available_guards_filter(guard, hr_guard):
    r = GuardRouter([guard, hr_guard], sticky=False)
    # Restrict to HR only — a forecast message has nowhere valid to land.
    res = r.route("查产品A的forecast", GuardContext(),
                  available_guards=["hr-agent-guard"])
    assert res.matched_guard is None


def test_list_guards(guard, hr_guard):
    r = GuardRouter([guard, hr_guard])
    assert set(r.list_guards()) == {"forecast-agent-guard", "hr-agent-guard"}
