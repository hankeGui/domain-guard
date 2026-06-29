"""Demo Web UI + FastAPI server."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .agent import ShopAgent
from .providers import make_provider

ROOT = Path(__file__).resolve().parent.parent

# One agent per session (in-memory; demo is single-process).
_sessions: dict[str, dict] = {}

# Aggregated stats (across all sessions) for the top-of-page counters.
_stats = {
    "total_messages": 0,
    "blocked_messages": 0,
    "llm_calls": 0,
    "tool_calls": 0,
    "tokens_in": 0,
    "tokens_out": 0,
    "saved_tokens": 0,
}

app = FastAPI(title="domain-guard demo: 电商客服 agent")


# ---- API ----

class ChatBody(BaseModel):
    session_id: str
    message: str
    guard_enabled: bool = True
    user_id: str = "u-alice"


@app.get("/api/data/conversations")
def conversations() -> dict:
    return json.loads((ROOT / "data" / "conversations.json").read_text(encoding="utf-8"))


@app.get("/api/data/orders")
def orders(user_id: str = "u-alice") -> dict:
    all_orders = json.loads((ROOT / "data" / "orders.json").read_text(encoding="utf-8"))
    return {"orders": [o for o in all_orders if o["user_id"] == user_id]}


@app.get("/api/stats")
def stats() -> dict:
    provider = make_provider()
    return {**_stats, "provider": provider.name}


@app.post("/api/session/reset")
def reset_session(body: dict) -> dict:
    sid = body.get("session_id", "default")
    _sessions.pop(sid, None)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatBody) -> JSONResponse:
    session = _sessions.setdefault(body.session_id, {
        "history": [],
        "agent_with_guard": ShopAgent(guard_enabled=True),
        "agent_no_guard": ShopAgent(guard_enabled=False),
    })

    agent = session["agent_with_guard"] if body.guard_enabled else session["agent_no_guard"]
    # Make sure the two agents share the same history (for fair comparison)
    history = session["history"]

    trace, history = agent.chat(body.message, history, user_id=body.user_id)
    session["history"] = history

    # Update aggregate stats
    _stats["total_messages"] += 1
    if trace.blocked:
        _stats["blocked_messages"] += 1
        _stats["saved_tokens"] += trace.saved_tokens_estimate
    _stats["llm_calls"] += sum(1 for e in trace.events if e.step == "llm")
    _stats["tool_calls"] += sum(1 for e in trace.events if e.step == "tool")
    _stats["tokens_in"] += trace.total_tokens_in
    _stats["tokens_out"] += trace.total_tokens_out

    return JSONResponse({
        "reply": trace.final_reply,
        "blocked": trace.blocked,
        "events": [asdict(e) for e in trace.events],
        "tokens_in": trace.total_tokens_in,
        "tokens_out": trace.total_tokens_out,
        "saved_tokens": trace.saved_tokens_estimate,
        "total_duration_ms": round(trace.total_duration_ms(), 1),
    })


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


def _main() -> None:
    import uvicorn
    port = int(os.environ.get("DEMO_PORT", "9000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    _main()
