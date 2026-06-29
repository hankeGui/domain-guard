"""GuardRouter — route a message to the right guard among several agents.

Each guard represents one agent (forecast, HR, IT-ticket, ...). The router
runs each guard's pipeline and picks the first one whose verdict is `pass`.
If all block, the router itself returns a block result with the highest-
confidence "alternative" surfaced for UI clarification.

Key features:
  - Sticky routing: once a session is routed to a guard, subsequent turns in
    that session go straight there (until intent_changes or session ends).
  - Optional `available_intents` filter on the request so the caller can scope
    routing dynamically (e.g. only try guards the user is allowed to use).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .context import GuardContext, GuardResult
from .core import DomainGuard


@dataclass
class RouteResult:
    matched_guard: str | None              # None if nothing passed
    guard_result: GuardResult | None       # the winner's result (or None)
    alternatives: list[tuple[str, GuardResult]] = field(default_factory=list)
    sticky_hit: bool = False

    @property
    def passed(self) -> bool:
        return self.matched_guard is not None


class GuardRouter:
    def __init__(
        self,
        guards: Iterable[DomainGuard],
        sticky: bool = True,
    ):
        self.guards: list[DomainGuard] = list(guards)
        self.sticky = sticky
        # Session → guard_name, for sticky routing across calls.
        self._sticky_map: dict[str, str] = {}

    # ---- main API ----

    def route(
        self,
        message: str,
        context: GuardContext | None = None,
        available_guards: list[str] | None = None,
    ) -> RouteResult:
        ctx = context or GuardContext()

        # Sticky: same session was already routed to a guard → keep going there
        if self.sticky and ctx.session_id:
            sticky_name = self._sticky_map.get(ctx.session_id)
            if sticky_name is not None:
                guard = next((g for g in self.guards if g.config.name == sticky_name), None)
                if guard is not None and (
                    available_guards is None or sticky_name in available_guards
                ):
                    result = guard.check(message, ctx)
                    if result.passed:
                        return RouteResult(
                            matched_guard=sticky_name,
                            guard_result=result,
                            sticky_hit=True,
                        )
                    # Sticky guard blocked → drop sticky and re-route fresh.
                    self._sticky_map.pop(ctx.session_id, None)

        # Fresh evaluation: try each candidate guard in order.
        alternatives: list[tuple[str, GuardResult]] = []
        for guard in self.guards:
            name = guard.config.name
            if available_guards is not None and name not in available_guards:
                continue
            result = guard.check(message, ctx)
            if result.passed:
                if self.sticky and ctx.session_id:
                    self._sticky_map[ctx.session_id] = name
                return RouteResult(matched_guard=name, guard_result=result)
            alternatives.append((name, result))

        # Nothing passed — sort alternatives by confidence so the caller can
        # show "did you mean X?" hints.
        alternatives.sort(key=lambda kv: -kv[1].confidence)
        return RouteResult(matched_guard=None, guard_result=None,
                           alternatives=alternatives)

    # ---- maintenance ----

    def clear_sticky(self, session_id: str | None = None) -> None:
        if session_id is None:
            self._sticky_map.clear()
        else:
            self._sticky_map.pop(session_id, None)

    def list_guards(self) -> list[str]:
        return [g.config.name for g in self.guards]
