"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

# Make the package importable when running from a fresh clone.
sys.path.insert(0, str(ROOT))

# Force the hash embedding backend everywhere so tests don't need network or
# the 100MB+ sentence-transformers model.
os.environ.setdefault("DOMAIN_GUARD_EMBEDDING", "hash")


# --------- shared fixtures ---------

@pytest.fixture
def guard():
    """A fresh DomainGuard built from the example forecast YAML."""
    from domain_guard import DomainGuard
    return DomainGuard.from_yaml(EXAMPLES / "forecast-agent.yaml")


@pytest.fixture
def hr_guard():
    from domain_guard import DomainGuard
    return DomainGuard.from_yaml(EXAMPLES / "hr-agent.yaml")


# --------- sidecar helpers ---------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, expected_guards: int = 1, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            h = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).json()
            if len(h.get("guards", [])) >= expected_guards:
                return h
        except Exception as e:
            last_err = e
        time.sleep(0.1)
    raise RuntimeError(f"sidecar didn't come up on :{port} (last error: {last_err})")


class Sidecar:
    """Helper for integration tests — start/stop a sidecar with custom env."""

    def __init__(self, port: int, base_url: str):
        self.port = port
        self.base_url = base_url

    def get(self, path: str, **kw):
        return httpx.get(f"{self.base_url}{path}", **kw)

    def post(self, path: str, **kw):
        return httpx.post(f"{self.base_url}{path}", **kw)

    def put(self, path: str, **kw):
        return httpx.put(f"{self.base_url}{path}", **kw)


@pytest.fixture
def sidecar(tmp_path, request):
    """Start a sidecar with a fresh GUARDS_DIR. Override env via marker:

        @pytest.mark.sidecar_env(RATE_LIMIT="5", HOT_RELOAD="1")
    """
    # Copy example guards into the test's tmp dir so tests can mutate freely.
    import shutil
    guards_dir = tmp_path / "guards"
    guards_dir.mkdir()
    for src in ["forecast-agent.yaml", "hr-agent.yaml"]:
        shutil.copy(EXAMPLES / src, guards_dir / src)

    port = _free_port()
    env = os.environ.copy()
    env.update({
        "DOMAIN_GUARD_EMBEDDING": "hash",
        "GUARDS_DIR": str(guards_dir),
        "PORT": str(port),
    })
    # Per-test env overrides via marker
    marker = request.node.get_closest_marker("sidecar_env")
    if marker:
        env.update(marker.kwargs)

    log_path = tmp_path / "sidecar.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "domain_guard.sidecar"],
        env=env, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
    )
    try:
        _wait_health(port, expected_guards=2)
        s = Sidecar(port=port, base_url=f"http://127.0.0.1:{port}")
        s.guards_dir = guards_dir          # type: ignore[attr-defined]
        s.log_path = log_path              # type: ignore[attr-defined]
        yield s
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sidecar_env(**env): per-test env overrides for the `sidecar` fixture",
    )
    config.addinivalue_line(
        "markers", "integration: spawns a real sidecar subprocess (slower)",
    )
