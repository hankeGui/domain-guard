"""Context bypass — if user is mid-flow, skip the guard."""

from __future__ import annotations

from ..context import GuardContext
from .base import Layer, LayerOutput


class ContextBypassLayer(Layer):
    """Pass-through when the agent is already in an active intent flow.

    Options:
        when: dict of state-field conditions. All must match.
              Use "not_null" / "null" / exact value.
              Example:
                when:
                  state.intent: not_null
                  state.stage: collecting_slots
    """

    name = "context_bypass"

    def setup(self) -> None:
        self.conditions: dict[str, object] = self.options.get("when", {}) or {}

    def decide(self, message: str, ctx: GuardContext) -> LayerOutput:
        if not self.conditions or not ctx.state:
            return LayerOutput(verdict="defer")

        for key, expected in self.conditions.items():
            actual = self._lookup(ctx, key)
            if not self._match(actual, expected):
                return LayerOutput(verdict="defer")

        return LayerOutput(
            verdict="pass",
            confidence=1.0,
            reason="in_active_flow",
        )

    @staticmethod
    def _lookup(ctx: GuardContext, dotted: str) -> object:
        # only support state.x for now
        parts = dotted.split(".")
        if parts[0] != "state":
            return None
        cur: object = ctx.state or {}
        for p in parts[1:]:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur

    @staticmethod
    def _match(actual: object, expected: object) -> bool:
        if expected == "not_null":
            return actual is not None
        if expected == "null":
            return actual is None
        return actual == expected
