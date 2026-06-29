"""Integration tests for sidecar auth — confirms 401 / 200 behaviour end to end."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


# ---- admin auth: writes require X-API-Key ----

@pytest.mark.sidecar_env(ADMIN_API_KEY="adminsecret")
def test_admin_required_blocks_unauthenticated_writes(sidecar):
    # Reads still work without a key.
    assert sidecar.get("/health").status_code == 200
    assert sidecar.get("/v1/guards/forecast-agent-guard/config").status_code == 200

    # Writes are blocked.
    r = sidecar.put("/v1/guards/forecast-agent-guard/config",
                    json={"content": "name: x\npipeline: []\nfallback:\n  reply: x\n"})
    assert r.status_code == 401

    r = sidecar.post("/v1/guards/forecast-agent-guard/reload")
    assert r.status_code == 401


@pytest.mark.sidecar_env(ADMIN_API_KEY="adminsecret")
def test_admin_writes_with_correct_key(sidecar):
    h = {"X-API-Key": "adminsecret"}
    r = sidecar.post("/v1/guards/forecast-agent-guard/reload", headers=h)
    assert r.status_code == 200
    assert r.json()["reloaded"] is True


@pytest.mark.sidecar_env(ADMIN_API_KEY="adminsecret")
def test_admin_wrong_key_rejected(sidecar):
    r = sidecar.post("/v1/guards/forecast-agent-guard/reload",
                     headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


@pytest.mark.sidecar_env(ADMIN_API_KEY="adminsecret")
def test_health_advertises_auth_required(sidecar):
    h = sidecar.get("/health").json()
    assert h["auth"]["admin_required"] is True
    assert h["auth"]["check_required"] is False


def test_default_no_auth(sidecar):
    # With no env vars set, /health reports auth disabled.
    h = sidecar.get("/health").json()
    assert h["auth"]["admin_required"] is False
    assert h["auth"]["check_required"] is False
    # And writes work without a key.
    r = sidecar.post("/v1/guards/forecast-agent-guard/reload")
    assert r.status_code == 200


# ---- check auth (optional) ----

@pytest.mark.sidecar_env(CHECK_API_KEY="checksecret")
def test_check_required_blocks_unauthenticated(sidecar):
    r = sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard", "message": "查产品",
    })
    assert r.status_code == 401


@pytest.mark.sidecar_env(CHECK_API_KEY="checksecret")
def test_check_passes_with_key(sidecar):
    r = sidecar.post("/v1/check",
                     json={"guard_id": "forecast-agent-guard", "message": "查产品"},
                     headers={"X-API-Key": "checksecret"})
    assert r.status_code == 200


@pytest.mark.sidecar_env(CHECK_API_KEY="checksecret")
def test_route_also_requires_check_key(sidecar):
    r = sidecar.post("/v1/route", json={"message": "我要请假"})
    assert r.status_code == 401
    r = sidecar.post("/v1/route", json={"message": "我要请假"},
                     headers={"X-API-Key": "checksecret"})
    assert r.status_code == 200
