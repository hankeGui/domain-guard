"""Per-message trace tracker. The UI reads this to draw the timeline."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceEvent:
    step: str            # "guard", "llm", "tool", "guard_blocked", ...
    label: str           # human-readable
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageTrace:
    user_message: str
    events: list[TraceEvent] = field(default_factory=list)
    final_reply: str = ""
    blocked: bool = False
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    saved_tokens_estimate: int = 0  # if blocked, what we would have spent
    started_at: float = field(default_factory=time.time)

    def add(self, ev: TraceEvent) -> None:
        self.events.append(ev)

    def total_duration_ms(self) -> float:
        return sum(e.duration_ms for e in self.events)


# Rough "what would the agent have spent" estimate for a blocked message.
# Used to make the savings visible in the UI. Order of magnitude is right
# for tool-using chat: 1 LLM call (intent) + 1 tool + 1 LLM call (reply).
ESTIMATED_BLOCKED_TOKENS_IN = 600
ESTIMATED_BLOCKED_TOKENS_OUT = 120
