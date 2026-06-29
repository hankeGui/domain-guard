# User Guide

A practical, task-oriented guide for putting domain-guard in front of an LLM agent.

> If you just want to evaluate the project, start with the README. This guide is for once you've decided to use it.

## Table of contents

1. [The 5-minute setup](#the-5-minute-setup)
2. [The guard YAML file](#the-guard-yaml-file)
3. [The four pipeline layers](#the-four-pipeline-layers)
4. [Choosing thresholds](#choosing-thresholds)
5. [Calibrating with real samples](#calibrating-with-real-samples)
6. [Shadow mode rollout](#shadow-mode-rollout)
7. [Running the sidecar](#running-the-sidecar)
8. [Calling from your code](#calling-from-your-code)
9. [Rate limiting](#rate-limiting)
10. [Multi-agent routing](#multi-agent-routing)
11. [The admin UI](#the-admin-ui)
12. [Observability](#observability)
13. [Troubleshooting](#troubleshooting)

---

## The 5-minute setup

```bash
git clone https://github.com/hankeGui/domain-guard.git
cd domain-guard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local]"        # core + local embedding model
```

Copy and edit one of the example guards:

```bash
cp examples/forecast-agent.yaml my-agent.yaml
# edit name, domain examples, fallback reply
```

Use it in three lines:

```python
from domain_guard import DomainGuard, GuardContext

guard = DomainGuard.from_yaml("my-agent.yaml")
result = guard.check("hello")
if not result.passed:
    return {"reply": result.fallback_reply}
# else: forward to your main LLM
```

---

## The guard YAML file

Each guard is a single YAML file. Minimum:

```yaml
name: my-guard
domain:
  description: "What this agent handles. The LLM fallback layer sees this string."
pipeline:
  - type: rule
    block_patterns: ["..."]
    allow_keywords: ["..."]
fallback:
  reply: "Sorry, I can't help with that."
  suggested_replies: []
```

### Top-level fields

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `name` | yes | — | Used as the guard ID over HTTP. Must be unique in the GUARDS_DIR. |
| `mode` | no | `enforce` | `shadow` records would-block decisions without actually blocking. |
| `domain.description` | no | empty | A short paragraph the LLM fallback layer is shown. |
| `pipeline` | yes | — | An ordered list of layers. See below. |
| `fallback.reply` | no | generic | The text shown to the user when the guard blocks. |
| `fallback.suggested_replies` | no | `[]` | Strings the UI can render as quick-reply buttons. |

---

## The four pipeline layers

Layers run top-to-bottom. Each layer returns **pass**, **block**, or **defer** (let the next layer try). The first non-defer verdict wins. If everything defers, the default is **block** (fail-closed).

### `context_bypass` — let in-flow turns through

Skip the guard when the agent is mid-conversation. Without this, a short follow-up like "ARE001" or "yes" would be misjudged as off-topic.

```yaml
- type: context_bypass
  when:
    state.intent: not_null         # match if state.intent is set
    state.stage: collecting_slots  # exact match
```

Supported matchers: exact value, `not_null`, `null`.

### `rule` — keyword & regex (microseconds)

```yaml
- type: rule
  block_patterns:
    - "你是.*(什么|哪个).*(模型|ai|llm)"
    - "ignore .* (previous|above)"
  allow_keywords:
    - forecast
    - 预测
    - ARE
  case_sensitive: false   # default
```

- A `block_pattern` regex match → **block** (with high confidence).
- Otherwise, a keyword match → **pass**.
- Otherwise → defer.

This is your cheapest layer. Keep it broad; embedding will catch the long tail.

### `embedding` — semantic similarity (~10 ms with local model)

```yaml
- type: embedding
  domain_examples:
    - "查询产品 forecast"
    - "更新 ARE001 的预测数据"
    - ...  # 20-50 examples covering typical phrasings
  ood_examples:           # optional, for clearer "off-topic" cases
    - "你是什么模型"
    - "讲个笑话"
  threshold:
    pass: 0.62
    block: 0.35
```

- If similarity to **any** domain example ≥ `pass` → **pass**.
- If similarity ≤ `block` → **block**.
- Between the two thresholds → **defer**.
- If an `ood_example` matches more closely than any domain example AND clears `pass`, it overrides with a **block**.

### `llm_fallback` — small LLM for ambiguity (~300 ms)

```yaml
- type: llm_fallback
  model: claude-haiku-4-5
```

Only triggers when the embedding layer deferred. The LLM is asked to return PASS or BLOCK based on `domain.description`. Useful for the ~5% of edge cases. Skip this layer if budget is tight.

---

## Choosing thresholds

Start loose, tighten over time. Reasonable defaults:

| Goal | Pass | Block |
|------|------|-------|
| Strict (cost-saving priority) | 0.70 | 0.45 |
| Balanced | 0.62 | 0.35 |
| Permissive (UX priority, fewer false blocks) | 0.55 | 0.25 |

Don't guess — use the calibration tool below.

---

## Calibrating with real samples

Collect 15–30 messages users send legitimately, and another 15–30 obvious off-topic ones. Put them in JSONL files:

```jsonl
{"message": "查产品A的forecast"}
{"message": "Forecast管理"}
```

Run:

```bash
guard-cli calibrate \
  --config my-agent.yaml \
  --positive samples/in_domain.jsonl \
  --negative samples/out_of_domain.jsonl
```

You'll see a table with accuracy at different `(pass, block)` cuts and a recommendation. Edge cases (close-to-threshold messages) are printed at the bottom — they tell you what examples to add to `domain_examples` / `ood_examples`.

---

## Shadow mode rollout

Before flipping a brand-new guard to enforce-block, run it in **shadow** for a week:

```yaml
mode: shadow
```

In shadow mode every "block" decision is recorded but the message is still passed through. Watch `/metrics` and `/v1/decisions/recent?only_blocked=true` to see what the guard *would* have blocked. Adjust the config until you're satisfied, then switch to `mode: enforce`.

---

## Running the sidecar

For non-Python agents (Node, Go, Java), run the HTTP service:

```bash
GUARDS_DIR=./guards \
HOT_RELOAD=1 \
CACHE_SIZE=2048 \
RATE_LIMIT=120 \
python -m domain_guard.sidecar
```

Environment variables:

| Var | Default | Notes |
|-----|---------|-------|
| `GUARDS_DIR` | `./guards` | All `*.yaml` files here are loaded as guards. |
| `PORT` | `8080` | |
| `CACHE_SIZE` | `1024` | LRU entries per guard. `0` disables. |
| `CACHE_TTL` | `3600` | Seconds. |
| `HOT_RELOAD` | `0` | `1` to watch GUARDS_DIR for changes. |
| `RATE_LIMIT` | `0` | Per-user/IP requests in `RATE_WINDOW`. `0` disables. |
| `RATE_WINDOW` | `60` | Seconds. |
| `RATE_BACKEND` | `memory` | Or `redis`. |
| `REDIS_URL` | — | Used when `RATE_BACKEND=redis`. |
| `DECISION_LOG_SIZE` | `500` | Ring buffer for the admin UI. |

---

## Calling from your code

### From Python

```python
from domain_guard import DomainGuard, GuardContext

guard = DomainGuard.from_yaml("my-agent.yaml")
result = guard.check(
    message=user_input,
    context=GuardContext(
        session_id="user-42",
        state={"intent": "query", "stage": "collecting_slots"},  # if any
    ),
)
if not result.passed:
    return result.fallback_reply
# else: hand off to your main LLM
```

### From any HTTP client

```bash
curl -X POST http://localhost:8080/v1/check \
  -H "content-type: application/json" \
  -d '{
    "guard_id": "my-agent",
    "message": "hello",
    "session_id": "user-42",
    "user_id": "alice"
  }'
```

Response:
```json
{
  "passed": false,
  "matched_layer": "rule",
  "confidence": 0.95,
  "reason": "matched_block_pattern:...",
  "fallback_reply": "Sorry, ...",
  "suggested_replies": ["..."],
  "latency_ms": 0.02,
  "cache_hit": false
}
```

---

## Rate limiting

Set `RATE_LIMIT=60 RATE_WINDOW=60` for "60 requests per minute per user."
The key is `user_id` if you send it, otherwise the client IP.

Rate-limited requests return HTTP **429** with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` headers.

For multi-instance deployments use `RATE_BACKEND=redis REDIS_URL=...` so all instances share the counter.

---

## Multi-agent routing

If you have multiple guards (forecast, HR, IT-ticket, ...), drop them all in `GUARDS_DIR` and hit `/v1/route` instead of `/v1/check`:

```bash
curl -X POST http://localhost:8080/v1/route \
  -d '{"message":"我要请假","session_id":"alice-1","user_id":"alice"}'
# → {"matched_guard":"hr-agent-guard", "passed":true, ...}
```

**Sticky routing** is on by default: once a session is routed to a guard, subsequent turns stay on that guard. This prevents "ARE001" in the middle of a forecast flow from being mis-routed to a different agent.

To scope routing per request, pass `available_guards: ["a", "b"]` — useful for per-tenant ACLs.

---

## The admin UI

Open `http://localhost:8080/admin`. Two tabs:

**Overview**
- Top-line metrics: total decisions, pass rate, blocked count, avg latency
- Breakdown by layer and by guard
- Live feed of recent decisions (auto-refresh every 3 s)
- Sidebar filter: per-guard / blocked-only / how many entries to show

**Config editor**
- Pick a guard in the sidebar — its YAML loads into the editor
- Edit and click **Save & reload**: backend validates the YAML, writes to disk, triggers a reload, returns success or a 400 with the parse error
- **Manual reload** re-reads the file without editing (useful when you `vi` it from a shell)

The admin endpoints are unauthenticated by default. **Don't expose them on a public network without adding a reverse-proxy with auth in front.**

---

## Observability

### Prometheus metrics

`/metrics` exposes:

```
domain_guard_checks_total{guard, verdict, layer, cache_hit}    counter
domain_guard_check_latency_ms{guard, verdict, cache_hit}        histogram
```

Useful Grafana panels:
- `rate(domain_guard_checks_total{verdict="block"}[5m])` — current block rate
- Sum by `cache_hit` — see how much cache is helping
- p95 of the latency histogram — performance regression alarm

### Decision log API

```bash
curl 'http://localhost:8080/v1/decisions/recent?limit=50&only_blocked=true'
```

Returns the last N decisions plus a summary (total / passed / blocked / by-layer / by-guard / avg-latency).

### Replay

To evaluate a change before shipping it:

```bash
# Compare two configs against the same traffic
guard-cli replay \
  --config new.yaml \
  --baseline current.yaml \
  --traffic last_week.jsonl

# Or compare new config against a `previous_decision` label in traffic
guard-cli replay --config new.yaml --traffic last_week.jsonl
```

Output lists every message whose verdict would flip — that's your review queue before going to production.

---

## Troubleshooting

**"Why was this message blocked?"**
Add `?debug=1` to `/v1/check` or call from Python and inspect `result.debug["layers"]`. You'll see each layer's verdict and confidence.

**"The embedding model takes forever to download."**
Set `DOMAIN_GUARD_EMBEDDING=hash` to fall back to a dependency-free hash embedder. It's lower quality but lets you develop offline. Switch back to sentence-transformers for production.

**"Hot reload isn't picking up my changes."**
The watcher polls every 2 s. APFS timestamps are second-level — if you save twice in the same second the second save can be missed. Check `/health` for the `reload_count`. As a workaround, hit `POST /v1/guards/{id}/reload` manually.

**"A legitimate message keeps getting blocked."**
1. Check `result.debug["layers"]` — which layer caught it?
2. If it's the rule layer, soften that regex or add an `allow_keyword`.
3. If it's the embedding layer with low domain similarity, add a similar example to `domain_examples` and recalibrate.
4. If you're in a slot-filling flow, make sure your `state` includes the keys mentioned in `context_bypass.when`.

**"The guard is too permissive."**
Tighten the `pass` threshold of the embedding layer. Add more `ood_examples`. Add specific patterns to `block_patterns`.
