"""Rule layer — regex block-list and keyword allow-list."""

from __future__ import annotations

import re

from ..context import GuardContext
from .base import Layer, LayerOutput


class RuleLayer(Layer):
    """Fast first-line filter.

    Options:
        block_patterns: list[str]  - regex; match → block
        allow_keywords: list[str]  - substring (case-insensitive); match → pass
        case_sensitive: bool       - default False
    """

    name = "rule"

    def setup(self) -> None:
        flags = 0 if self.options.get("case_sensitive") else re.IGNORECASE
        self._block_res = [
            re.compile(p, flags) for p in self.options.get("block_patterns", []) or []
        ]
        keywords = self.options.get("allow_keywords", []) or []
        if self.options.get("case_sensitive"):
            self._allow_kws = list(keywords)
        else:
            self._allow_kws = [k.lower() for k in keywords]
        self._cs = bool(self.options.get("case_sensitive"))

    def decide(self, message: str, ctx: GuardContext) -> LayerOutput:
        # 1) block list wins (defensive)
        for pat in self._block_res:
            if pat.search(message):
                return LayerOutput(
                    verdict="block",
                    confidence=0.95,
                    reason=f"matched_block_pattern:{pat.pattern}",
                )

        # 2) allow keywords
        hay = message if self._cs else message.lower()
        for kw in self._allow_kws:
            if kw in hay:
                return LayerOutput(
                    verdict="pass",
                    confidence=0.9,
                    reason=f"matched_allow_keyword:{kw}",
                )

        return LayerOutput(verdict="defer")
