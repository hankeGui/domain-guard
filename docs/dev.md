# Developer Guide

For contributors and people extending `domain-guard`.

## Table of contents

1. [Project layout](#project-layout)
2. [Architecture overview](#architecture-overview)
3. [Adding a new pipeline layer](#adding-a-new-pipeline-layer)
4. [Adding a new provider](#adding-a-new-provider)
5. [Adding a new sidecar endpoint](#adding-a-new-sidecar-endpoint)
6. [Testing](#testing)
7. [Versioning & release](#versioning--release)
8. [Design principles](#design-principles)

---

## Project layout

```
domain_guard/
├── __init__.py            re-exports DomainGuard, GuardContext, GuardResult
├── core.py                DomainGuard main class + check() pipeline driver
├── config.py              YAML loader → GuardConfig dataclass
├── context.py             GuardContext (input) and GuardResult (output)
├── cache.py               LRUResultCache + RedisResultCache + cache key derivation
├── decision_log.py        bounded ring buffer powering the admin UI
├── metrics.py             optional Prometheus integration (no-op if lib absent)
├── ratelimit.py           TokenBucketLimiter + RedisRateLimiter
├── router.py              GuardRouter (multi-guard, sticky session)
├── replay.py              guard-cli replay implementation
├── calibrate.py           guard-cli calibrate implementation
├── cli.py                 dispatcher for the guard-cli console entry point
├── sidecar.py             FastAPI app: /v1/check, /v1/route, /admin, /metrics, ...
├── admin.html             single-page admin UI (vanilla JS)
├── layers/
│   ├── base.py            Layer abstract class & LayerOutput
│   ├── context_bypass.py  L0 — skip pipeline when mid-flow
│   ├── rule.py            L1 — regex block / keyword allow
│   ├── embedding.py       L2 — semantic similarity vs domain examples
│   └── llm_fallback.py    L3 — small-LLM tiebreaker
└── providers/
    ├── embedding_local.py LocalEmbeddingProvider (sentence-transformers)
    │                       + HashEmbeddingProvider (offline fallback)
    └── llm_claude.py      ClaudeLLMProvider

examples/
├── forecast-agent.yaml    reference guard YAML
├── hr-agent.yaml          second guard for multi-routing demos
├── forecast-agent-old.yaml  baseline used in replay examples
├── finsense_gateway.py    reverse-proxy gateway pattern
├── run_demo.py            quick smoke check from a fresh clone
└── samples/               labeled traffic for calibrate + replay

tests/
├── conftest.py            pytest fixtures (guard, hr_guard, sidecar)
├── unit/                  fast, in-process tests per module
└── integration/           tests that spawn a real sidecar subprocess

docs/
├── user-guide.md          for people USING the project
├── dev.md                 you are here
└── ai-dev.md              for AI coding agents working in this repo
```

---

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                          DomainGuard                              │
│                                                                   │
│   check(message, ctx)                                             │
│       │                                                           │
│       │  ┌─ cache lookup ────────────────────────────────────┐   │
│       │  │ key = sha1(name + message + state_signature)      │   │
│       │  └────────────────────────────────────────────────────┘   │
│       │                                                           │
│       ▼                                                           │
│   ┌─────────────────────────────────────────────────────────┐    │
│   │ Pipeline: ordered list of Layer instances                │    │
│   │                                                          │    │
│   │   layer.decide(message, ctx) → "pass" | "block" | "defer"│    │
│   │                                                          │    │
│   │   first non-defer wins; all-defer → BLOCK (fail-closed) │    │
│   └─────────────────────────────────────────────────────────┘    │
│       │                                                           │
│       ▼                                                           │
│   notify observers (metrics, decision log) → store in cache       │
│       │                                                           │
│       ▼                                                           │
│   return GuardResult                                              │
└──────────────────────────────────────────────────────────────────┘
```

Key invariants:
- Layers are **pure** — they take `(message, ctx)` and return a verdict. No side effects.
- `DomainGuard.check()` is **thread-safe** (the cache and observer list are locked; layers are read-only after construction).
- Observers are **best-effort** — exceptions inside an observer are swallowed, never crash a check.

### Where state lives

| State | Where | Notes |
|-------|-------|-------|
| Compiled regex / embedding vectors | `Layer` instance | Built once in `Layer.setup()`. |
| Loaded guards | `GuardRegistry` (sidecar singleton) | Mutated on hot reload. |
| Result cache | `ResultCache` attached to a guard | Optional. |
| Decision log | `DecisionLog` singleton in sidecar | Bounded ring buffer (default 500). |
| Rate limit buckets | `RateLimiter` singleton in sidecar | In-memory or Redis. |
| Prometheus counters | global registry | One process. |

---

## Adding a new pipeline layer

Three steps.

### 1. Subclass `Layer`

```python
# domain_guard/layers/pii_filter.py
from .base import Layer, LayerOutput
from ..context import GuardContext

class PIIFilterLayer(Layer):
    name = "pii_filter"

    def setup(self) -> None:
        # Read options, precompile patterns, fetch the model, whatever.
        self.deny_patterns = self.options.get("deny", [])

    def decide(self, message: str, ctx: GuardContext) -> LayerOutput:
        for pat in self.deny_patterns:
            if pat in message:
                return LayerOutput(verdict="block", confidence=0.95,
                                   reason=f"pii_match:{pat}")
        return LayerOutput(verdict="defer")
```

The `name` attribute is what shows up in metrics labels and `result.matched_layer`.

### 2. Register it

```python
# domain_guard/layers/__init__.py
from .pii_filter import PIIFilterLayer

LAYER_REGISTRY = {
    "context_bypass": ContextBypassLayer,
    "rule": RuleLayer,
    "embedding": EmbeddingLayer,
    "llm_fallback": LLMFallbackLayer,
    "pii_filter": PIIFilterLayer,  # ← new
}
```

### 3. Use it from YAML

```yaml
pipeline:
  - type: pii_filter
    deny: ["social security", "credit card"]
```

### 4. Test it

Add a `tests/unit/test_pii_filter.py`. The `Layer` interface makes this trivial — no fixtures needed beyond a couple of dicts.

### Conventions for new layers

- Layers should be **fast**. If yours is > 100 ms, document it and let it run last.
- Return `defer` liberally — that's how you cooperate with the layers below you.
- Pass `confidence ∈ [0, 1]`. The router and metrics use it.
- Put any expensive setup (model loading, regex compilation) in `setup()`, not `decide()`.

---

## Adding a new provider

Providers are the swappable backends behind layers (embedding model, LLM, cache backend, rate-limit backend, ...).

Embedding example:

```python
# domain_guard/providers/embedding_openai.py
import numpy as np

class OpenAIEmbeddingProvider:
    def __init__(self, model="text-embedding-3-small", api_key=None):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self.model = model

    def embed(self, texts):
        resp = self._client.embeddings.create(model=self.model, input=list(texts))
        return np.array([d.embedding for d in resp.data], dtype=np.float32)
```

Then use it explicitly:

```python
guard = DomainGuard.from_yaml(
    "g.yaml",
    providers={"embedding": OpenAIEmbeddingProvider()},
)
```

If you want it picked up automatically by config, extend `domain_guard/providers/__init__.py:make_default_embedding()` to honor a new env var.

The interface is implicit (duck-typed) — `embed(texts: list[str]) -> np.ndarray` is the whole contract.

---

## Adding a new sidecar endpoint

`sidecar.py` is just FastAPI. Add a route, declare your Pydantic body model, and you're done:

```python
class MyBody(BaseModel):
    foo: str

@app.post("/v1/my-thing")
def my_thing(req: MyBody) -> dict:
    assert registry is not None
    return {"echo": req.foo, "guards": registry.list_ids()}
```

Then add an integration test under `tests/integration/`. Use the `sidecar` fixture — it gives you a running instance with two example guards loaded.

If your endpoint mutates server state (writes a YAML, clears a cache, ...), prefer **POST** for the verb and **return a structured response with what changed** so the admin UI can react.

---

## Testing

```bash
.venv/bin/pytest tests/                # everything
.venv/bin/pytest tests/unit/           # fast subset (< 1 s)
.venv/bin/pytest tests/integration/    # spawns sidecar (~1 min)
.venv/bin/pytest -k cache              # by name pattern
```

### Fixture map

| Fixture | Scope | What it gives you |
|---------|-------|-------------------|
| `guard` | function | A fresh `DomainGuard` built from `examples/forecast-agent.yaml` |
| `hr_guard` | function | Same for HR config |
| `sidecar` | function | A subprocess sidecar with both example guards loaded; `Sidecar.get/post/put` helpers |

### Per-test sidecar env

```python
@pytest.mark.sidecar_env(RATE_LIMIT="5", HOT_RELOAD="1")
def test_rate_limit_kicks_in(sidecar):
    ...
```

The marker is consumed by the `sidecar` fixture; kwargs become env vars in the subprocess.

### Don't use the real embedding model in tests

`tests/conftest.py` sets `DOMAIN_GUARD_EMBEDDING=hash`. The hash embedder is fast, offline, and good enough to verify pipeline behavior. The real model gets exercised by ad-hoc local runs.

---

## Versioning & release

This project uses semver.

- Patch (x.y.**Z**): bug fixes, doc updates, internal refactors.
- Minor (x.**Y**.0): new layers/providers/endpoints, additive config fields.
- Major (**X**.0.0): breaking config schema or API changes.

To cut a release:

```bash
# 1. Update CHANGELOG.md with the new section
# 2. Bump version in pyproject.toml
# 3. Tag
git tag -a v0.2.0 -m "v0.2.0"
git push --tags
```

CI doesn't auto-publish to PyPI yet — release artifacts live on the GitHub release page.

---

## Design principles

**Each layer should be cheap or last.**
The whole point is to avoid the expensive main LLM. If a layer costs more than a default LLM call, it shouldn't run on every request.

**Fail closed.**
When uncertain, block. The cost of "didn't help one user" is low; the cost of "burned tokens on chit-chat" is what the project exists to fix.

**Config over code.**
A new domain shouldn't require a Python change. If you find yourself reaching for code where YAML could express it, push the knob into the YAML.

**No required dependencies for the core.**
`pip install domain-guard` should work with `pyyaml` and `numpy` only. Embedding, LLM, sidecar, Redis are all opt-in extras.

**Observable by default.**
Every check produces a `GuardResult` with `matched_layer`, `confidence`, `reason`, `latency_ms`, and `debug`. Observers (metrics, decision log) consume these without the layer authors having to know about them.

**State is external.**
The library is stateless. State (cache, decision log, rate-limit buckets, sticky-routing map) lives in pluggable backends that you can scale separately.

---

## Where to start contributing

Easy wins:
- More example YAMLs for different domains (e.g. customer support, internal IT)
- New `block_patterns` for common attack phrasings
- Better defaults for the hash embedder thresholds

Medium:
- Auth middleware for the admin endpoints (`X-API-Key` header or similar)
- New layer: `safety_classifier` wrapping LlamaGuard / ShieldGemma
- New provider: OpenAI / Azure / Cohere embeddings

Big:
- A web UI that's nicer than the single-page admin.html
- Per-tenant ACL and YAML scoping
- gRPC / streaming variants of the sidecar API
