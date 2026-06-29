# Changelog

All notable changes to this project will be documented in this file. Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Auth** for the sidecar:
  - `ADMIN_API_KEY` env var requires `X-API-Key` for write endpoints
    (`PUT /v1/guards/{id}/config`, `POST /v1/guards/{id}/reload`).
  - Optional `CHECK_API_KEY` extends the same check to `/v1/check` and
    `/v1/route`.
  - Admin UI input field for the key (persisted in `localStorage`).
  - `/health` advertises which auth modes are required.
- **More example guards** demonstrating the library across domains:
  `customer-support.yaml`, `it-ticket.yaml`, `coding-assistant.yaml`.
- **PyPI publish workflow** (`.github/workflows/publish.yml`) using OIDC
  trusted publishing — `v*` tag pushes auto-publish to PyPI.
- 22 new tests (7 unit auth + 8 integration auth + 7 example sanity).

## [0.1.0] — 2026-06-29

Initial public release.

### Added

- **Core library**
  - `DomainGuard` with a 4-layer pipeline: `context_bypass` → `rule` → `embedding` → `llm_fallback`
  - Fail-closed default: undecided messages are blocked
  - Shadow mode (`mode: shadow`) — records would-block decisions without blocking
  - YAML-driven config; one guard = one file
  - Pluggable observer system (metrics, decision log) via `DomainGuard.add_observer`
- **Providers**
  - `LocalEmbeddingProvider` (sentence-transformers)
  - `HashEmbeddingProvider` (zero-dependency offline fallback for tests/demos)
  - `ClaudeLLMProvider` (Anthropic SDK)
- **Result cache**
  - `LRUResultCache` (in-process, thread-safe)
  - `RedisResultCache` (optional, lazy import)
  - State-aware cache keys so different `state` values don't collide
- **Rate limiting**
  - `TokenBucketLimiter` (in-process)
  - `RedisRateLimiter` (fixed-window, shared across instances)
  - Per-`user_id` keying with IP fallback
- **Multi-guard routing**
  - `GuardRouter` with sticky session affinity
  - `available_guards` filter for per-request scoping
- **Sidecar (FastAPI)**
  - `POST /v1/check`, `POST /v1/route`
  - `GET /v1/guards`, `GET /health`
  - `GET /metrics` (Prometheus exposition)
  - `GET /v1/decisions/recent` — bounded ring buffer of recent decisions
  - `GET/PUT /v1/guards/{id}/config` — read & write guard YAMLs
  - `POST /v1/guards/{id}/reload` — manual reload
  - `GET /admin` — single-page admin UI (vanilla JS, no build step)
- **Hot reload** — polls `GUARDS_DIR` every ~2 s
- **Observability** — Prometheus counters/histograms with `guard`, `verdict`, `layer`, `cache_hit` labels
- **CLI**
  - `guard-cli check` — single-message smoke check
  - `guard-cli calibrate` — sweep `(pass, block)` thresholds against labeled samples
  - `guard-cli replay` — re-evaluate historical traffic against a new config; A/B compare two configs
- **Examples**
  - `examples/forecast-agent.yaml`, `examples/hr-agent.yaml`
  - `examples/finsense_gateway.py` — reverse-proxy gateway demo
  - `examples/run_demo.py` — sanity-check 11 cases from a fresh clone
- **Documentation**
  - `docs/user-guide.md`
  - `docs/dev.md`
  - `docs/ai-dev.md`
- **Tests** — 67 tests (46 unit + 21 integration)
