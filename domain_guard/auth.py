"""Auth helpers for the sidecar.

Design:
- One env var: ADMIN_API_KEY. If set, write endpoints require X-API-Key header.
  If unset, auth is disabled (development / single-user mode).
- Optional CHECK_API_KEY for /v1/check and /v1/route — disabled by default
  since most deployments want guard checks to flow freely.

Why X-API-Key not Bearer? Simpler ops: kubectl secrets, env vars, curl with -H
all work without extra ceremony. For OAuth/JWT, put a reverse proxy in front.
"""

from __future__ import annotations

import hmac
import os

try:
    from fastapi import HTTPException, Request
except ImportError:  # pragma: no cover
    HTTPException = Request = None  # type: ignore


_ADMIN_KEY_ENV = "ADMIN_API_KEY"
_CHECK_KEY_ENV = "CHECK_API_KEY"


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _read_key(env_name: str) -> str | None:
    raw = os.environ.get(env_name, "").strip()
    return raw or None


def require_admin(request: "Request") -> None:
    """Dependency for write endpoints. No-op if ADMIN_API_KEY is not set."""
    expected = _read_key(_ADMIN_KEY_ENV)
    if expected is None:
        return
    presented = request.headers.get("x-api-key", "")
    if not presented or not _consteq(presented, expected):
        raise HTTPException(401, {"error": "admin auth required"})


def require_check(request: "Request") -> None:
    """Dependency for /v1/check, /v1/route. No-op if CHECK_API_KEY is not set."""
    expected = _read_key(_CHECK_KEY_ENV)
    if expected is None:
        return
    presented = request.headers.get("x-api-key", "")
    if not presented or not _consteq(presented, expected):
        raise HTTPException(401, {"error": "check auth required"})


def admin_auth_enabled() -> bool:
    return _read_key(_ADMIN_KEY_ENV) is not None


def check_auth_enabled() -> bool:
    return _read_key(_CHECK_KEY_ENV) is not None
