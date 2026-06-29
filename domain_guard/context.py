"""Core dataclasses: GuardContext (input) and GuardResult (output)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuardContext:
    """Everything a layer might need to make a decision.

    All fields are optional — pass what you have.
    """

    session_id: str | None = None
    state: dict[str, Any] | None = None
    history: list[dict[str, Any]] | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardResult:
    """Outcome of running the pipeline on one message."""

    passed: bool
    matched_layer: str | None = None
    confidence: float = 0.0
    reason: str = ""
    fallback_reply: str | None = None
    suggested_replies: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    debug: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed
