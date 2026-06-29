"""Embedding similarity layer — semantic match against domain examples."""

from __future__ import annotations

import numpy as np

from ..context import GuardContext
from .base import Layer, LayerOutput


class EmbeddingLayer(Layer):
    """Compare query embedding to in-domain (and optional out-of-domain) examples.

    Options:
        domain_examples: list[str]              required
        ood_examples: list[str]                 optional
        threshold:
            pass: float   (>= this → pass)
            block: float  (<= this → block; only meaningful with ood_examples
                           or as a hard lower bound on domain similarity)
    """

    name = "embedding"

    def setup(self) -> None:
        self.embedder = self.providers.get("embedding")
        if self.embedder is None:
            raise RuntimeError("EmbeddingLayer needs an 'embedding' provider")

        domain_examples = self.options.get("domain_examples") or []
        if not domain_examples:
            raise ValueError("EmbeddingLayer needs domain_examples")
        ood_examples = self.options.get("ood_examples") or []

        thr = self.options.get("threshold") or {}
        self.thr_pass = float(thr.get("pass", 0.72))
        self.thr_block = float(thr.get("block", 0.50))

        self._domain_vecs = self._normalize(self.embedder.embed(domain_examples))
        self._ood_vecs = (
            self._normalize(self.embedder.embed(ood_examples)) if ood_examples else None
        )

    def decide(self, message: str, ctx: GuardContext) -> LayerOutput:
        q = self._normalize(self.embedder.embed([message]))[0]

        domain_sim = float(np.max(self._domain_vecs @ q))
        ood_sim = (
            float(np.max(self._ood_vecs @ q)) if self._ood_vecs is not None else 0.0
        )

        debug = {"domain_sim": domain_sim, "ood_sim": ood_sim}

        # OOD beats domain by a clear margin → block
        if self._ood_vecs is not None and ood_sim > domain_sim and ood_sim >= self.thr_pass:
            return LayerOutput(
                verdict="block",
                confidence=ood_sim,
                reason=f"closer_to_ood({ood_sim:.2f} > domain {domain_sim:.2f})",
                debug=debug,
            )

        if domain_sim >= self.thr_pass:
            return LayerOutput(
                verdict="pass",
                confidence=domain_sim,
                reason=f"domain_sim={domain_sim:.2f}",
                debug=debug,
            )

        if domain_sim <= self.thr_block:
            return LayerOutput(
                verdict="block",
                confidence=1.0 - domain_sim,
                reason=f"domain_sim_too_low={domain_sim:.2f}",
                debug=debug,
            )

        return LayerOutput(
            verdict="defer",
            confidence=domain_sim,
            reason=f"ambiguous(domain={domain_sim:.2f})",
            debug=debug,
        )

    @staticmethod
    def _normalize(arr: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.clip(norms, 1e-12, None)
        return arr / norms
