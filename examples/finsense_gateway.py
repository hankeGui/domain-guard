"""FinSense gateway: front the real /api/finsense/agent/chat with a domain guard.

This demonstrates how to drop domain-guard in front of an existing agent API
WITHOUT changing the upstream service. Off-topic requests are answered locally
(no upstream call, no LLM token spent); on-topic requests are forwarded as-is.

Run:
    UPSTREAM=https://gbs-ai-test.siemens.com.cn/api/finsense/agent/chat \\
    .venv/bin/uvicorn examples.finsense_gateway:app --port 9000
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from domain_guard import DomainGuard, GuardContext


GUARD_PATH = Path(__file__).parent / "forecast-agent.yaml"
UPSTREAM_URL = os.environ.get(
    "UPSTREAM",
    "https://gbs-ai-test.siemens.com.cn/api/finsense/agent/chat",
)

guard = DomainGuard.from_yaml(GUARD_PATH)
app = FastAPI(title="FinSense Gateway with Domain Guard")


@app.post("/api/finsense/agent/chat")
async def chat(request: Request) -> JSONResponse:
    body: dict[str, Any] = await request.json()

    # 'start' events bypass the guard — they're session initialization.
    if body.get("event") == "start":
        return await _forward(request, body)

    message = body.get("message", "") or ""
    state = body.get("state") or {}

    result = guard.check(
        message,
        GuardContext(
            session_id=body.get("session_id"),
            state=state,
        ),
    )

    if not result.passed:
        # Build a response that matches the upstream's contract, so the frontend
        # can render it without special-casing.
        return JSONResponse({
            "reply": result.fallback_reply,
            "state": state,
            "missing_slots": [],
            "suggested_replies": result.suggested_replies,
            "action": None,
            "completed": False,
            # Optional metadata — useful for observability, ignored by old frontends.
            "_guard": {
                "blocked_by": result.matched_layer,
                "confidence": round(result.confidence, 3),
                "reason": result.reason,
                "latency_ms": round(result.latency_ms, 2),
            },
        })

    # On-topic: forward to the real backend untouched.
    return await _forward(request, body)


async def _forward(request: Request, body: dict[str, Any]) -> JSONResponse:
    # Pass-through cookies & relevant headers so the upstream's auth still works.
    headers = {
        "content-type": "application/json",
        "accept": request.headers.get("accept", "application/json"),
    }
    if cookie := request.headers.get("cookie"):
        headers["cookie"] = cookie

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(UPSTREAM_URL, json=body, headers=headers)
    return JSONResponse(
        content=resp.json() if "application/json" in resp.headers.get("content-type", "")
                            else {"raw": resp.text},
        status_code=resp.status_code,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "guard": guard.config.name, "upstream": UPSTREAM_URL}
