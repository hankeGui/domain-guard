# domain-guard

[![CI](https://github.com/hankeGui/domain-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/hankeGui/domain-guard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Pluggable **domain guard** for LLM agents. Blocks off-topic requests **before** they hit your expensive main model — saving tokens, money, and latency.

```
user input
   ↓
[L0 context bypass]   already in an active flow → pass
   ↓
[L1 rule]             regex block / keyword allow — microseconds
   ↓
[L2 embedding]        semantic similarity vs domain examples — milliseconds
   ↓
[L3 llm_fallback]     small LLM (Claude Haiku) for ambiguous cases
   ↓
main agent (expensive)
```

Every layer can return `pass` / `block` / `defer`. First non-defer wins. If everything defers, the default is **block** (fail-closed).

## Documentation

- **[Quick demo](demo/README.md)** — 5-minute hands-on: a working e-commerce-support agent with mock data, web UI, and live token savings counter. No code changes — just fill in an API key (or run in mock mode without one) and open `http://localhost:9000`.
- **[User guide](docs/user-guide.md)** — 5-minute setup, YAML reference, sidecar deployment, troubleshooting
- **[Developer guide](docs/dev.md)** — architecture, extending layers/providers, contributing
- **[AI agent guide](docs/ai-dev.md)** — for Claude Code / Copilot / Cursor working in this repo

## Install

```bash
pip install domain-guard[local]    # core + local sentence-transformers (once on PyPI)

# Or from source:
git clone https://github.com/hankeGui/domain-guard.git
cd domain-guard
pip install -e ".[local]"          # core + local sentence-transformers
pip install -e ".[local,claude]"   # + Claude Haiku LLM fallback
pip install -e ".[sidecar]"        # + FastAPI sidecar
pip install -e ".[all]"            # everything
```

## 30-second use

```python
from domain_guard import DomainGuard, GuardContext

guard = DomainGuard.from_yaml("examples/forecast-agent.yaml")

result = guard.check(
    message="你是什么模型",
    context=GuardContext(state={"intent": None, "stage": "awaiting_intent"}),
)
if not result.passed:
    return {
        "reply": result.fallback_reply,
        "suggested_replies": result.suggested_replies,
        "blocked_by": result.matched_layer,   # "rule" / "embedding" / "llm_fallback"
    }
# else: forward to your main agent
```

## YAML config

A guard is a single YAML file. See `examples/forecast-agent.yaml`.

```yaml
name: forecast-agent-guard
mode: enforce        # or "shadow" — record decisions without actually blocking
domain:
  description: "FinSense product & Forecast data management"
pipeline:
  - type: context_bypass
    when: { state.stage: collecting_slots }
  - type: rule
    block_patterns: ["你是.*模型", "ignore .* (previous|above)"]
    allow_keywords: ["forecast", "预测", "ARE"]
  - type: embedding
    domain_examples: ["查询产品 forecast", "更新 ARE001 的预测", ...]
    ood_examples:    ["你是什么模型", "讲个笑话", ...]
    threshold: { pass: 0.62, block: 0.35 }
  - type: llm_fallback     # optional
    model: claude-haiku-4-5
fallback:
  reply: "抱歉，我专注于 Forecast 管理..."
  suggested_replies: ["Forecast管理", "产品Forecast预估"]
```

## Demo

```bash
DOMAIN_GUARD_EMBEDDING=hash .venv/bin/python examples/run_demo.py
```

Runs 11 test cases against the FinSense guard. `hash` uses a dependency-free
fallback embedder for offline demos; drop the env var to use the real model.

## Calibrate thresholds

Got labeled samples? Find the best `pass`/`block` cuts automatically.

```bash
guard-cli calibrate \
  --config examples/forecast-agent.yaml \
  --positive examples/samples/in_domain.jsonl \
  --negative examples/samples/out_of_domain.jsonl
```

Output:
```
Samples: 20 positive, 20 negative
Decided by earlier layers: context_bypass=0, rule_pass=19, rule_block=7
Threshold sweep (...)
→ Recommended:  pass=0.65  block=0.30
  accuracy:    97.5%
  误杀率:      0.0%
  漏放率:      5.0%
```

## Sidecar (non-Python agents)

Run the guard as an HTTP service so Node / Go / Java agents can use it:

```bash
GUARDS_DIR=./examples \
HOT_RELOAD=1 \
CACHE_SIZE=2048 \
.venv/bin/python -m domain_guard.sidecar
```

Endpoints:
- `POST /v1/check` — run a check (see CheckRequest)
- `GET  /v1/guards` — list loaded guards
- `GET  /health` — health + reload counter
- `GET  /metrics` — Prometheus exposition

```bash
curl -X POST http://localhost:8080/v1/check \
  -H "content-type: application/json" \
  -d '{"guard_id":"forecast-agent-guard","message":"你是什么模型"}'
```

## Result cache

`DomainGuard` accepts an optional cache. Repeats hit cached results instead of
re-running the pipeline. State is included in the cache key, so the same
message in different states is treated as different.

```python
from domain_guard import DomainGuard
from domain_guard.cache import LRUResultCache, RedisResultCache

guard = DomainGuard.from_yaml("g.yaml", cache=LRUResultCache(max_size=1024, ttl_seconds=3600))
# or:
guard = DomainGuard.from_yaml("g.yaml", cache=RedisResultCache(url="redis://localhost:6379/0"))
```

The sidecar wires an LRU cache automatically (see `CACHE_SIZE` / `CACHE_TTL`
env vars).

## Prometheus metrics

The sidecar exports the standard Prometheus exposition format on `/metrics`:

```
domain_guard_checks_total{guard, verdict, layer, cache_hit}     # counter
domain_guard_check_latency_ms{guard, verdict, cache_hit}        # histogram
```

Scrape from Prometheus / Grafana as usual.

## Hot reload

Run the sidecar with `HOT_RELOAD=1` and any change to a `*.yaml` in
`GUARDS_DIR` will be picked up within ~2s. Deleting a file unloads its guard.
The current count is visible at `/health`.

## Replay

Rerun historical traffic through a new config to see what would change before
you ship it.

```bash
guard-cli replay \
  --config examples/forecast-agent.yaml \
  --baseline examples/forecast-agent-old.yaml \
  --traffic examples/samples/traffic.jsonl
```

Two modes:
- **A/B**: pass `--baseline` to compare against another config; output lists
  flipped decisions (`pass → block`, `block → pass`) and layer transitions.
- **Single config**: omit `--baseline`; flips are compared against a
  `previous_decision` field in each traffic record (handy when you log the
  guard's old decision and want to evaluate a new config against it).

## Rate limit

Token-bucket limiter in front of `/v1/check` and `/v1/route`. Keyed by
`user_id` (or client IP if absent). Configure via env vars on the sidecar:

```bash
RATE_LIMIT=60          # requests per window per user
RATE_WINDOW=60         # window seconds
RATE_BACKEND=memory    # or "redis"
REDIS_URL=redis://...  # when RATE_BACKEND=redis
```

Rate-limited responses get `429` plus `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining` headers.

## Multi-guard routing

When you have multiple agents (forecast, HR, IT, ...), load all their YAMLs
into `GUARDS_DIR` and hit `/v1/route`. The router picks the first guard whose
pipeline passes the message.

```bash
curl -X POST http://localhost:8080/v1/route \
  -H "content-type: application/json" \
  -d '{"message":"我要请假","session_id":"alice-1","user_id":"alice"}'
# → {"matched_guard":"hr-agent-guard", "passed":true, ...}
```

Sticky routing: once a session is routed to a guard, subsequent turns stay on
that guard (until it explicitly blocks or you call `clear_sticky`). Pass
`available_guards: [...]` in the request to scope routing per-call.

## Admin UI

Open `http://localhost:8080/admin` for a single-page admin dashboard:

- Live decision feed with per-layer / per-guard counts and pass-rate
- Inspect & edit any guard's YAML in the browser
- Save → validates → writes to disk → triggers reload
- Manual "Reload" button per guard
- Filters: blocked-only, per-guard, last N entries

Backed by:
- `GET /v1/decisions/recent?limit=&guard=&only_blocked=`
- `GET/PUT /v1/guards/{id}/config`
- `POST /v1/guards/{id}/reload`

### Securing the admin endpoints

In production, set `ADMIN_API_KEY` so that write operations require an
`X-API-Key` header:

```bash
ADMIN_API_KEY=<long random> python -m domain_guard.sidecar
```

Read endpoints (`/health`, `/metrics`, `GET .../config`) remain open so
monitoring still works. Optionally also set `CHECK_API_KEY` to require the
same header on `/v1/check` and `/v1/route`. The admin UI has an input field
at the top-right to set the key from the browser (stored in `localStorage`).

## Gateway pattern (drop in front of an existing agent)

`examples/finsense_gateway.py` shows how to put the guard in front of an
existing `/api/finsense/agent/chat` endpoint without changing the upstream
service. Off-topic requests are answered locally; on-topic requests are
forwarded as-is. End-to-end test:

```bash
DOMAIN_GUARD_EMBEDDING=hash .venv/bin/python examples/test_gateway.py
```

## Project layout

```
domain_guard/
├── core.py              DomainGuard main class
├── config.py            YAML loader
├── context.py           GuardContext / GuardResult
├── layers/              context_bypass, rule, embedding, llm_fallback
├── providers/           sentence-transformers, hash fallback, Claude
├── calibrate.py         threshold sweeping
├── cli.py               guard-cli entry point
└── sidecar.py           FastAPI HTTP service
examples/
├── forecast-agent.yaml
├── run_demo.py
├── finsense_gateway.py
├── test_gateway.py
└── samples/             labeled JSONL for calibration
```

## Roadmap

- [x] Layered pipeline (context_bypass / rule / embedding / llm_fallback)
- [x] YAML config, pluggable providers
- [x] `shadow` mode for safe rollout
- [x] `guard-cli calibrate` threshold tuner
- [x] FastAPI sidecar
- [x] Gateway example
- [x] Result cache (LRU in-process + optional Redis)
- [x] Prometheus metrics on `/metrics`
- [x] Hot reload on YAML change
- [x] `guard-cli replay` — re-evaluate historical traffic
- [x] Rate limit / per-user quota (memory + Redis)
- [x] Multi-guard router with sticky session routing
- [x] Admin UI for live monitoring + config editing
- [x] Auth on admin endpoints (X-API-Key)
- [x] More example agents (forecast / HR / customer-support / IT / coding)
- [ ] PyPI release automation (workflow in place, awaiting first tag)
- [ ] Per-route metrics labels (which guard the router picked)

## License

MIT — see [LICENSE](LICENSE).
