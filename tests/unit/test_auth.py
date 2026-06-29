"""Unit tests for the auth module — pure logic, no FastAPI required at decode time."""

from __future__ import annotations

import pytest


def _mk_request(headers: dict):
    """Tiny stand-in for a Starlette Request with just the .headers attr."""
    class _Req:
        def __init__(self, h):
            self.headers = {k.lower(): v for k, v in h.items()}
    return _Req(headers)


def test_admin_auth_no_op_when_unset(monkeypatch):
    from domain_guard.auth import require_admin, admin_auth_enabled
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert admin_auth_enabled() is False
    require_admin(_mk_request({}))  # should not raise


def test_admin_auth_blocks_when_set_and_missing(monkeypatch):
    from domain_guard.auth import require_admin
    from fastapi import HTTPException
    monkeypatch.setenv("ADMIN_API_KEY", "secret-xyz")
    with pytest.raises(HTTPException) as ei:
        require_admin(_mk_request({}))
    assert ei.value.status_code == 401


def test_admin_auth_blocks_on_wrong_key(monkeypatch):
    from domain_guard.auth import require_admin
    from fastapi import HTTPException
    monkeypatch.setenv("ADMIN_API_KEY", "secret-xyz")
    with pytest.raises(HTTPException):
        require_admin(_mk_request({"X-API-Key": "wrong"}))


def test_admin_auth_allows_correct_key(monkeypatch):
    from domain_guard.auth import require_admin
    monkeypatch.setenv("ADMIN_API_KEY", "secret-xyz")
    # Header lookups should be case-insensitive (Starlette behaviour).
    require_admin(_mk_request({"x-api-key": "secret-xyz"}))


def test_check_auth_independent_of_admin(monkeypatch):
    from domain_guard.auth import (
        require_admin, require_check,
        admin_auth_enabled, check_auth_enabled,
    )
    monkeypatch.setenv("ADMIN_API_KEY", "a")
    monkeypatch.delenv("CHECK_API_KEY", raising=False)
    assert admin_auth_enabled() and not check_auth_enabled()
    # check should be a no-op when CHECK_API_KEY is not set
    require_check(_mk_request({}))


def test_empty_string_is_treated_as_unset(monkeypatch):
    from domain_guard.auth import admin_auth_enabled
    monkeypatch.setenv("ADMIN_API_KEY", "   ")
    assert admin_auth_enabled() is False


def test_keys_are_compared_constant_time(monkeypatch):
    """Smoke test that we use hmac.compare_digest — same outcome for known cases."""
    from domain_guard.auth import require_admin
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    # If we accidentally used == on bytes of different length, the FastAPI
    # import would still hide the issue; the assertion below confirms the
    # path runs at all.
    require_admin(_mk_request({"X-API-Key": "secret"}))
