"""Layer base class. Each layer returns one of three verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..context import GuardContext

Verdict = Literal["pass", "block", "defer"]


@dataclass
class LayerOutput:
    verdict: Verdict
    confidence: float = 0.0
    reason: str = ""
    debug: dict[str, Any] | None = None


class Layer:
    """Base class. Subclasses implement `decide`."""

    name: str = "layer"

    def __init__(self, options: dict[str, Any], providers: dict[str, Any]):
        self.options = options
        self.providers = providers
        self.setup()

    def setup(self) -> None:
        """Override for one-time init (e.g. precompute embeddings)."""

    def decide(self, message: str, ctx: GuardContext) -> LayerOutput:
        raise NotImplementedError
