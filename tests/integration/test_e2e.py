"""Integration tests for hot reload, rate limit, and gateway pattern."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml


pytestmark = pytest.mark.integration


# ---- rate limit ----

@pytest.mark.sidecar_env(RATE_LIMIT="3", RATE_WINDOW="60")
def test_rate_limit_kicks_in(sidecar):
    body = {"guard_id": "forecast-agent-guard",
            "message": "查产品A的forecast", "user_id": "alice"}
    statuses = []
    for _ in range(5):
        statuses.append(sidecar.post("/v1/check", json=body).status_code)
    # First 3 → 200, then 429s
    assert statuses[:3] == [200, 200, 200]
    assert all(s == 429 for s in statuses[3:])


@pytest.mark.sidecar_env(RATE_LIMIT="2", RATE_WINDOW="60")
def test_rate_limit_per_user(sidecar):
    body_alice = {"guard_id": "forecast-agent-guard",
                  "message": "查产品", "user_id": "alice"}
    body_bob = {**body_alice, "user_id": "bob"}
    for _ in range(2):
        assert sidecar.post("/v1/check", json=body_alice).status_code == 200
    assert sidecar.post("/v1/check", json=body_alice).status_code == 429
    # Bob has his own bucket
    assert sidecar.post("/v1/check", json=body_bob).status_code == 200


@pytest.mark.sidecar_env(RATE_LIMIT="1", RATE_WINDOW="60")
def test_rate_limit_response_headers(sidecar):
    body = {"guard_id": "forecast-agent-guard", "message": "查产品", "user_id": "z"}
    sidecar.post("/v1/check", json=body)  # use up the token
    r = sidecar.post("/v1/check", json=body)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


# ---- hot reload ----

@pytest.mark.sidecar_env(HOT_RELOAD="1")
def test_hot_reload_picks_up_changes(sidecar):
    # Confirm a message is blocked
    r1 = sidecar.post("/v1/check", json={
        "guard_id": "forecast-agent-guard", "message": "讲个笑话",
    }).json()
    assert not r1["passed"]

    # Mutate the YAML — remove the block pattern and add as keyword
    yaml_path = Path(sidecar.guards_dir) / "forecast-agent.yaml"  # type: ignore
    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    for layer in cfg["pipeline"]:
        if layer["type"] == "rule":
            layer["block_patterns"] = [p for p in layer["block_patterns"]
                                       if "笑话" not in p]
            layer["allow_keywords"] = list(layer["allow_keywords"]) + ["笑话"]
            break
    time.sleep(1.1)  # ensure mtime moves forward on APFS
    yaml_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    # Wait for watcher (polls every 2s)
    passed = False
    for _ in range(15):
        time.sleep(0.5)
        r = sidecar.post("/v1/check", json={
            "guard_id": "forecast-agent-guard", "message": "讲个笑话",
        }).json()
        if r["passed"]:
            passed = True
            break
    assert passed
    health = sidecar.get("/health").json()
    assert health["reload_count"] >= 1


# ---- gateway pattern ----

def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_gateway_pattern_blocks_off_topic(tmp_path):
    """Smaller, self-contained gateway test.

    Run mock upstream in a thread (it has no signal-handling needs) and the
    gateway in a subprocess so its FastAPI app can install signal handlers
    properly on the main thread of that process.
    """
    import threading

    ROOT = Path(__file__).resolve().parent.parent.parent

    # ---- mock upstream in a thread ----
    upstream_port = _free_port()
    gateway_port = _free_port()

    mock_upstream_src = f'''\
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

calls_path = sys.argv[1]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw): pass
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw or "{{}}")
        with open(calls_path, "a", encoding="utf-8") as f:
            f.write(raw + "\\n")
        resp = {{
            "reply": "[upstream] " + repr(body.get("message")),
            "state": body.get("state") or {{}},
            "missing_slots": [], "suggested_replies": [],
            "action": None, "completed": False,
        }}
        out = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

ThreadingHTTPServer(("127.0.0.1", {upstream_port}), H).serve_forever()
'''
    upstream_script = tmp_path / "upstream.py"
    upstream_script.write_text(mock_upstream_src, encoding="utf-8")
    calls_log = tmp_path / "calls.jsonl"
    upstream_proc = subprocess.Popen(
        [sys.executable, str(upstream_script), str(calls_log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for upstream
    for _ in range(80):
        try:
            r = httpx.post(
                f"http://127.0.0.1:{upstream_port}/api/finsense/agent/chat",
                json={"message": "ping"}, timeout=0.5,
            )
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        upstream_proc.terminate()
        pytest.fail("upstream did not come up")

    # Clear ping call so we count only real ones below.
    calls_log.write_text("", encoding="utf-8")

    # ---- gateway in a subprocess ----
    env = os.environ.copy()
    env["UPSTREAM"] = f"http://127.0.0.1:{upstream_port}/api/finsense/agent/chat"
    env["DOMAIN_GUARD_EMBEDDING"] = "hash"
    env["PYTHONPATH"] = str(ROOT)
    gw_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "examples.finsense_gateway:app",
         "--host", "127.0.0.1", "--port", str(gateway_port),
         "--log-level", "warning"],
        env=env, cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{gateway_port}/health",
                             timeout=0.5).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("gateway did not come up")

        state = {"stage": "awaiting_intent", "intent": None, "slots": {},
                 "pending_operation": None, "last_query": None}

        # Off-topic: blocked by guard, never reach upstream.
        r = httpx.post(
            f"http://127.0.0.1:{gateway_port}/api/finsense/agent/chat",
            json={"event": "message", "message": "你是什么模型",
                  "messages": [], "state": state, "session_id": "s1"},
            timeout=5,
        )
        assert r.status_code == 200
        assert r.json()["_guard"]["blocked_by"] == "rule"
        # Upstream should have zero calls.
        assert calls_log.read_text().strip() == ""

        # On-topic: forwarded.
        r = httpx.post(
            f"http://127.0.0.1:{gateway_port}/api/finsense/agent/chat",
            json={"event": "message", "message": "查产品A的forecast",
                  "messages": [], "state": state, "session_id": "s1"},
            timeout=5,
        )
        assert r.status_code == 200, r.text
        assert "[upstream]" in r.json()["reply"]
        recorded = [ln for ln in calls_log.read_text().splitlines() if ln.strip()]
        assert len(recorded) == 1
    finally:
        gw_proc.terminate()
        upstream_proc.terminate()
        for p in (gw_proc, upstream_proc):
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
