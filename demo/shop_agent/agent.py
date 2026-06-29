"""Demo agent main loop:  guard → LLM → tool → LLM → reply

Designed so the UI can render each step on a timeline.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from domain_guard import DomainGuard, GuardContext

from .providers import LLMProvider, ToolCall, make_provider
from .tools import TOOL_SPECS, dispatch
from .tracker import (
    ESTIMATED_BLOCKED_TOKENS_IN,
    ESTIMATED_BLOCKED_TOKENS_OUT,
    MessageTrace,
    TraceEvent,
)


GUARD_PATH = Path(__file__).resolve().parent.parent / "guards" / "shop-support.yaml"


class ShopAgent:
    def __init__(
        self,
        guard: DomainGuard | None = None,
        provider: LLMProvider | None = None,
        guard_enabled: bool = True,
    ):
        self.guard = guard or DomainGuard.from_yaml(GUARD_PATH)
        self.provider = provider or make_provider()
        self.guard_enabled = guard_enabled

    def chat(
        self,
        user_message: str,
        history: list[dict],
        user_id: str = "u-alice",
    ) -> tuple[MessageTrace, list[dict]]:
        """One user turn → one assistant reply, updating history."""
        trace = MessageTrace(user_message=user_message)

        # ---- Step 1: domain guard ----
        if self.guard_enabled:
            t0 = time.perf_counter()
            ctx = GuardContext(
                session_id=user_id, user_id=user_id,
                state=_state_for(history),
            )
            result = self.guard.check(user_message, ctx)
            trace.add(TraceEvent(
                step="guard" if result.passed else "guard_blocked",
                label=("放行" if result.passed
                       else f"拦截 — {result.matched_layer or 'fail-closed'}"),
                duration_ms=(time.perf_counter() - t0) * 1000,
                detail={
                    "passed": result.passed,
                    "matched_layer": result.matched_layer,
                    "confidence": result.confidence,
                    "reason": result.reason,
                },
            ))

            if not result.passed:
                trace.blocked = True
                trace.final_reply = result.fallback_reply or "I can't help with that."
                trace.saved_tokens_estimate = (
                    ESTIMATED_BLOCKED_TOKENS_IN + ESTIMATED_BLOCKED_TOKENS_OUT
                )
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": trace.final_reply})
                return trace, history

        # ---- Step 2 onwards: agent loop ----
        history.append({"role": "user", "content": user_message})

        # Up to 4 iterations of (LLM → maybe tool calls → LLM ...)
        for iteration in range(4):
            t0 = time.perf_counter()
            llm_messages = _scope_history_for_llm(history, user_id)
            resp = self.provider.chat(llm_messages, tools=TOOL_SPECS)
            trace.total_tokens_in += resp.input_tokens
            trace.total_tokens_out += resp.output_tokens

            trace.add(TraceEvent(
                step="llm",
                label=f"LLM 调用 #{iteration+1}",
                duration_ms=(time.perf_counter() - t0) * 1000,
                detail={
                    "provider": self.provider.name,
                    "tool_calls": [tc.name for tc in resp.tool_calls],
                    "tokens_in": resp.input_tokens,
                    "tokens_out": resp.output_tokens,
                },
            ))

            if resp.tool_calls:
                # Record this assistant turn (text + tool_use blocks)
                history.append({
                    "role": "assistant_tool_call",
                    "content": {
                        "text": resp.text or "",
                        "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args}
                                       for tc in resp.tool_calls],
                    },
                })
                # Run each tool call, append results
                for tc in resp.tool_calls:
                    tt0 = time.perf_counter()
                    result = dispatch(tc.name, tc.args, user_id=user_id)
                    trace.add(TraceEvent(
                        step="tool",
                        label=f"工具调用 {tc.name}",
                        duration_ms=(time.perf_counter() - tt0) * 1000,
                        detail={
                            "name": tc.name, "args": tc.args,
                            "result_preview": _short(result),
                        },
                    ))
                    history.append({
                        "role": "tool", "tool_call_id": tc.id, "content": result,
                    })
                # Loop again so the LLM can summarize the tool result
                continue

            # No tool calls → this is the final reply
            trace.final_reply = resp.text or "(empty)"
            history.append({"role": "assistant", "content": trace.final_reply})
            break
        else:
            # Hit iteration limit
            trace.final_reply = "(agent reached iteration limit without finalizing)"
            history.append({"role": "assistant", "content": trace.final_reply})

        return trace, history


# ---------------- helpers ----------------

def _state_for(history: list[dict]) -> dict[str, Any]:
    """Build the GuardContext.state for this turn.

    Earlier versions tried to auto-flip into `collecting_slots` whenever the
    last assistant message looked like a follow-up question. That turned out
    to be far too permissive: as soon as the agent said "哪一个呢？" *any*
    next user message was waved through, including blatantly off-topic ones
    like "今天天气怎么样".

    The guard works perfectly well on each message individually thanks to
    the rule-layer keywords (e.g. "ord-" covers short order-id replies), so
    we leave context_bypass disarmed here. A real agent that does explicit
    slot filling should pass state={"stage": "collecting_slots"} *only*
    while it is actively waiting for a specific named slot — not whenever
    it asks a follow-up question.
    """
    return {"intent": None, "stage": "awaiting_intent"}


def _scope_history_for_llm(history: list[dict], user_id: str) -> list[dict]:
    """Prepend a context note about the user. LLM providers translate the rest."""
    note = {"role": "user", "content": f"(系统提示：当前用户 user_id = {user_id})"}
    return [note] + history


def _short(obj: Any, n: int = 200) -> str:
    s = str(obj)
    return s if len(s) <= n else s[:n] + "..."
