"""End-to-end tests that spawn a real sidecar process."""

from __future__ import annotations

import json
import time

import pytest


pytestmark = pytest.mark.integration


# ---- /v1/check happy path ----

def test_check_endpoint_basic(sidecar):
    r = sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard",
        "message": "查产品A的forecast",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["matched_layer"] == "rule"
    assert body["cache_hit"] is False


def test_check_endpoint_blocks_off_topic(sidecar):
    r = sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard",
        "message": "你是什么模型",
    })
    body = r.json()
    assert body["passed"] is False
    assert body["fallback_reply"]


def test_check_endpoint_unknown_guard(sidecar):
    r = sidecar.post("/v1/check", json={"guard_id": "nope", "message": "x"})
    assert r.status_code == 404


def test_health_lists_guards(sidecar):
    h = sidecar.get("/health").json()
    assert "forecast-agent-guard" in h["guards"]
    assert "hr-agent-guard" in h["guards"]


def test_list_guards(sidecar):
    r = sidecar.get("/v1/guards").json()
    assert set(r["guards"]) == {"forecast-agent-guard", "hr-agent-guard"}


def test_debug_flag(sidecar):
    r = sidecar.post("/v1/check?debug=1", json={
        "guard_id": "forecast-agent-guard",
        "message": "帮我看下数据",
    }).json()
    assert r["debug"] is not None
    assert "layers" in r["debug"]


# ---- /v1/route ----

def test_route_picks_correct_guard(sidecar):
    r = sidecar.post("/v1/route", json={
        "message": "我要请假", "session_id": "test1",
    }).json()
    assert r["matched_guard"] == "hr-agent-guard"


def test_route_all_block_returns_alternatives(sidecar):
    r = sidecar.post("/v1/route", json={
        "message": "你是什么模型", "session_id": "test2",
    }).json()
    assert r["matched_guard"] is None
    assert len(r["alternatives"]) >= 2


def test_route_sticky_session(sidecar):
    sidecar.post("/v1/route", json={
        "message": "查产品A的forecast", "session_id": "sticky1",
    })
    r = sidecar.post("/v1/route", json={
        "message": "ARE001", "session_id": "sticky1",
        "state": {"intent": "forecast_management", "stage": "collecting_slots"},
    }).json()
    assert r["matched_guard"] == "forecast-agent-guard"
    assert r["sticky_hit"] is True


# ---- /metrics ----

def test_metrics_endpoint(sidecar):
    # Generate one check first
    sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard", "message": "test",
    })
    m = sidecar.get("/metrics").text
    assert "domain_guard_checks_total" in m


# ---- admin endpoints ----

def test_recent_decisions(sidecar):
    for msg in ["查产品", "你是什么模型", "我要请假"]:
        sidecar.post("/v1/check", json={
            "guard_id": "forecast-agent-guard", "message": msg,
        })
    r = sidecar.get("/v1/decisions/recent?limit=5").json()
    assert r["summary"]["total"] >= 3
    assert len(r["items"]) >= 1


def test_get_config(sidecar):
    r = sidecar.get("/v1/guards/forecast-agent-guard/config").json()
    assert "forecast-agent-guard" in r["content"]
    assert r["guard_id"] == "forecast-agent-guard"


def test_put_config_valid(sidecar):
    orig = sidecar.get("/v1/guards/forecast-agent-guard/config").json()["content"]
    # Insert a deterministic marker we can read back. We don't care WHERE
    # in the reply it lands, only that the reload picked it up.
    marker = "[admin-modified-marker]"
    modified = orig.replace("您好", f"{marker}您好", 1)
    if marker not in modified:
        # Fallback: just append the marker into the existing reply value.
        modified = orig.replace("Forecast", f"{marker}Forecast", 1)
    r = sidecar.put("/v1/guards/forecast-agent-guard/config",
                    json={"content": modified})
    assert r.status_code == 200
    assert r.json()["reloaded"] is True

    # Verify new reply is in effect
    ck = sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard", "message": "你是什么模型",
    }).json()
    assert marker in ck["fallback_reply"]


def test_put_config_invalid_rejected(sidecar):
    r = sidecar.put("/v1/guards/forecast-agent-guard/config",
                    json={"content": "not: [valid: yaml"})
    assert r.status_code == 400


def test_manual_reload(sidecar):
    before = sidecar.get("/health").json()["reload_count"]
    r = sidecar.post("/v1/guards/forecast-agent-guard/reload").json()
    assert r["reloaded"] is True
    after = sidecar.get("/health").json()["reload_count"]
    assert after > before


def test_admin_html(sidecar):
    r = sidecar.get("/admin")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert "domain-guard admin" in r.text
