"""Sanity tests for the example guard YAMLs.

Each example must:
  1) load without errors
  2) pass at least one representative in-domain message
  3) block at least one representative off-topic message
  4) correctly route via GuardRouter when loaded alongside other agents
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain_guard import DomainGuard, GuardContext
from domain_guard.router import GuardRouter


EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"


# (config_filename, sample_in_domain, sample_off_topic, name)
EXAMPLE_CASES = [
    ("forecast-agent.yaml",       "查产品A的forecast",    "你是什么模型",
        "forecast-agent-guard"),
    ("hr-agent.yaml",             "我要请假",            "查 forecast",
        "hr-agent-guard"),
    ("customer-support.yaml",     "我的订单怎么还没到",   "推荐一个手机",
        "customer-support-guard"),
    ("it-ticket.yaml",            "VPN 连不上",          "帮我 review 代码",
        "it-ticket-guard"),
    ("coding-assistant.yaml",     "帮我写一个 Python 快排", "我心情不好",
        "coding-assistant-guard"),
]


@pytest.mark.parametrize("filename,in_domain,off_topic,name", EXAMPLE_CASES)
def test_example_loads_and_filters(filename, in_domain, off_topic, name):
    g = DomainGuard.from_yaml(EXAMPLES / filename)
    assert g.config.name == name

    # In-domain message passes
    r = g.check(in_domain)
    assert r.passed, f"{filename}: {in_domain!r} should pass"

    # Off-topic message blocked
    r = g.check(off_topic)
    assert not r.passed, f"{filename}: {off_topic!r} should block"


def test_examples_are_distinct_in_router():
    """Loading all 5 examples into one router, each in-domain message must be
    routed to its own guard. This is the real "library is generic" assertion.
    """
    guards = [DomainGuard.from_yaml(EXAMPLES / f)
              for f, _, _, _ in EXAMPLE_CASES]
    router = GuardRouter(guards, sticky=False)

    for filename, in_domain, _off, expected_name in EXAMPLE_CASES:
        res = router.route(in_domain, GuardContext())
        assert res.matched_guard == expected_name, (
            f"{in_domain!r} expected {expected_name}, got {res.matched_guard}"
        )


def test_off_topic_message_blocks_all_guards():
    """A message that's off-topic for every agent should produce no match."""
    guards = [DomainGuard.from_yaml(EXAMPLES / f)
              for f, _, _, _ in EXAMPLE_CASES]
    router = GuardRouter(guards, sticky=False)
    # "你是什么模型" is in every config's block_patterns
    res = router.route("你是什么模型", GuardContext())
    assert res.matched_guard is None
    assert len(res.alternatives) == len(EXAMPLE_CASES)
