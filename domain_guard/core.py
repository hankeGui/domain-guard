"""DomainGuard — main entry point.

Usage:
    guard = DomainGuard.from_yaml("forecast-agent.yaml")
    result = guard.check(message, GuardContext(...))
    if not result.passed:
        return result.fallback_reply
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cache import ResultCache, make_cache_key
from .config import GuardConfig
from .context import GuardContext, GuardResult
from .layers import LAYER_REGISTRY, Layer


class DomainGuard:
    def __init__(
        self,
        config: GuardConfig,
        providers: dict[str, Any] | None = None,
        cache: ResultCache | None = None,
    ):
        self.config = config
        self.providers = self._resolve_providers(providers or {}, config)
        self.layers: list[Layer] = self._build_layers()
        self.cache = cache
        # Multi-observer hook: append callables of (guard_name, message, result, cache_hit).
        # The message is passed to allow logging/UI; metrics ignore it.
        self._observers: list = []

    def add_observer(self, fn) -> None:
        """Register an observer fn(guard_name, message, result, cache_hit)."""
        self._observers.append(fn)

    # Back-compat single-hook setter (used by MetricsCollector.attach).
    @property
    def _metrics_hook(self):
        return self._observers[0] if self._observers else None

    @_metrics_hook.setter
    def _metrics_hook(self, fn):
        # Setting None used to clear; preserve that.
        if fn is None:
            self._observers.clear()
        else:
            # Replace whatever single hook was there; if multiple observers
            # exist, replace just the first slot to keep the same semantics.
            if self._observers:
                self._observers[0] = fn
            else:
                self._observers.append(fn)

    # ---- construction helpers ----

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        providers: dict[str, Any] | None = None,
        cache: ResultCache | None = None,
    ) -> "DomainGuard":
        return cls(GuardConfig.from_yaml(path), providers=providers, cache=cache)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        providers: dict[str, Any] | None = None,
        cache: ResultCache | None = None,
    ) -> "DomainGuard":
        return cls(GuardConfig.from_dict(data), providers=providers, cache=cache)

    def _resolve_providers(
        self,
        provided: dict[str, Any],
        config: GuardConfig,
    ) -> dict[str, Any]:
        """Lazily build default providers only for layers that need them."""
        resolved = dict(provided)
        layer_types = {lc.type for lc in config.pipeline}

        if "embedding" in layer_types and "embedding" not in resolved:
            from .providers import make_default_embedding
            resolved["embedding"] = make_default_embedding()

        if "llm_fallback" in layer_types and "llm" not in resolved:
            from .providers import make_default_llm
            resolved["llm"] = make_default_llm()

        return resolved

    def _build_layers(self) -> list[Layer]:
        out: list[Layer] = []
        for lc in self.config.pipeline:
            cls = LAYER_REGISTRY.get(lc.type)
            if cls is None:
                raise ValueError(f"Unknown layer type: {lc.type}")
            opts = dict(lc.options)
            # inject domain_description into llm_fallback if not set explicitly
            if lc.type == "llm_fallback" and "domain_description" not in opts:
                opts["domain_description"] = self.config.domain_description
            out.append(cls(options=opts, providers=self.providers))
        return out

    # ---- main API ----

    def check(self, message: str, context: GuardContext | None = None) -> GuardResult:
        ctx = context or GuardContext()
        started = time.perf_counter()

        cache_key: str | None = None
        if self.cache is not None:
            cache_key = make_cache_key(self.config.name, message, ctx)
            cached = self.cache.get(cache_key)
            if cached is not None:
                result = replace(
                    cached,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    debug={**(cached.debug or {}), "cache_hit": True},
                )
                self._notify(message, result, cache_hit=True)
                return result

        debug: dict[str, Any] = {"layers": []}

        for layer in self.layers:
            out = layer.decide(message, ctx)
            debug["layers"].append(
                {
                    "layer": layer.name,
                    "verdict": out.verdict,
                    "confidence": out.confidence,
                    "reason": out.reason,
                    "debug": out.debug,
                }
            )
            if out.verdict == "pass":
                result = self._result(True, layer.name, out, started, debug)
                self._store_and_emit(cache_key, message, result)
                return result
            if out.verdict == "block":
                result = self._result(False, layer.name, out, started, debug)
                self._store_and_emit(cache_key, message, result)
                return result
            # defer → continue

        # All layers deferred — default to BLOCK (fail-closed).
        result = GuardResult(
            passed=False,
            matched_layer=None,
            confidence=0.0,
            reason="all_layers_deferred",
            fallback_reply=self.config.fallback.reply,
            suggested_replies=list(self.config.fallback.suggested_replies),
            latency_ms=(time.perf_counter() - started) * 1000,
            debug=debug,
        )
        self._store_and_emit(cache_key, message, result)
        return result

    def _store_and_emit(self, cache_key: str | None, message: str,
                        result: GuardResult) -> None:
        if self.cache is not None and cache_key is not None:
            self.cache.set(cache_key, result)
        self._notify(message, result, cache_hit=False)

    def _notify(self, message: str, result: GuardResult, cache_hit: bool) -> None:
        for fn in list(self._observers):
            try:
                # Try the new 4-arg signature first; fall back to 3-arg (legacy).
                try:
                    fn(self.config.name, message, result, cache_hit)
                except TypeError:
                    fn(self.config.name, result, cache_hit)
            except Exception:
                pass

    def _result(
        self,
        passed: bool,
        layer_name: str,
        out,
        started: float,
        debug: dict[str, Any],
    ) -> GuardResult:
        # In shadow mode, never actually block — but record the decision.
        if not passed and self.config.mode == "shadow":
            debug["shadow_would_block"] = True
            return GuardResult(
                passed=True,
                matched_layer=layer_name,
                confidence=out.confidence,
                reason=f"shadow:{out.reason}",
                latency_ms=(time.perf_counter() - started) * 1000,
                debug=debug,
            )

        return GuardResult(
            passed=passed,
            matched_layer=layer_name,
            confidence=out.confidence,
            reason=out.reason,
            fallback_reply=None if passed else self.config.fallback.reply,
            suggested_replies=[] if passed else list(self.config.fallback.suggested_replies),
            latency_ms=(time.perf_counter() - started) * 1000,
            debug=debug,
        )
