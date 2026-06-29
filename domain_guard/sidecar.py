"""FastAPI sidecar exposing DomainGuard as an HTTP service.

Run:
    uvicorn domain_guard.sidecar:app --port 8080
or:
    GUARDS_DIR=./examples python -m domain_guard.sidecar

Environment variables:
    GUARDS_DIR        Directory of *.yaml guard configs (default: ./guards)
    PORT              HTTP port (default: 8080)
    CACHE_SIZE        LRU max size, 0 disables cache (default: 1024)
    CACHE_TTL         LRU TTL in seconds (default: 3600)
    HOT_RELOAD        "1" to watch GUARDS_DIR for changes (default: 0)
    RATE_LIMIT        per-user requests per window, 0 disables (default: 0)
    RATE_WINDOW       window seconds (default: 60)
    RATE_BACKEND      "memory" or "redis" (default: memory)
    REDIS_URL         used when RATE_BACKEND=redis
    ADMIN_API_KEY     required for write endpoints (PUT/POST guards/...). If
                      unset, write endpoints are unauthenticated.
    CHECK_API_KEY     optional; if set, /v1/check and /v1/route require it too.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

try:
    from fastapi import Depends, FastAPI, HTTPException, Request, Response
    from pydantic import BaseModel, Field
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "Sidecar requires fastapi/uvicorn. Install with: pip install domain-guard[sidecar]"
    ) from e

from .auth import admin_auth_enabled, check_auth_enabled, require_admin, require_check
from .cache import LRUResultCache
from .context import GuardContext
from .core import DomainGuard
from .decision_log import DecisionLog
from .metrics import MetricsCollector
from .ratelimit import RateLimiter, TokenBucketLimiter
from .router import GuardRouter

log = logging.getLogger("domain_guard.sidecar")


# ---------------- request/response schemas ----------------

class CheckRequest(BaseModel):
    guard_id: str = Field(..., description="Name of the loaded guard config")
    message: str
    session_id: str | None = None
    state: dict[str, Any] | None = None
    history: list[dict[str, Any]] | None = None
    user_id: str | None = None
    metadata: dict[str, Any] | None = None


class CheckResponse(BaseModel):
    passed: bool
    matched_layer: str | None
    confidence: float
    reason: str
    fallback_reply: str | None
    suggested_replies: list[str]
    latency_ms: float
    cache_hit: bool = False
    debug: dict[str, Any] | None = None


class RouteRequest(BaseModel):
    message: str
    session_id: str | None = None
    state: dict[str, Any] | None = None
    user_id: str | None = None
    available_guards: list[str] | None = Field(
        default=None,
        description="Optional whitelist of guard names to consider.",
    )


class RouteAlternative(BaseModel):
    guard: str
    confidence: float
    reason: str


class RouteResponse(BaseModel):
    matched_guard: str | None
    passed: bool
    matched_layer: str | None = None
    confidence: float = 0.0
    reason: str = ""
    fallback_reply: str | None = None
    suggested_replies: list[str] = []
    sticky_hit: bool = False
    alternatives: list[RouteAlternative] = []


# ---------------- guard registry ----------------

class GuardRegistry:
    """Thread-safe registry of loaded guards, with optional file watching."""

    def __init__(self, cache_size: int, cache_ttl: float, metrics: MetricsCollector):
        self._guards: dict[str, DomainGuard] = {}
        self._guard_files: dict[str, Path] = {}   # guard name -> source path
        self._file_mtime: dict[Path, float] = {}  # path -> mtime
        self._lock = threading.Lock()
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl
        self._metrics = metrics
        self._watch_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.reload_count = 0

    def _make_cache(self) -> LRUResultCache | None:
        if self._cache_size <= 0:
            return None
        return LRUResultCache(max_size=self._cache_size, ttl_seconds=self._cache_ttl)

    def _load_file(self, path: Path) -> bool:
        try:
            guard = DomainGuard.from_yaml(path, cache=self._make_cache())
        except Exception as e:
            log.error("Failed to load %s: %s", path, e)
            return False
        self._metrics.attach(guard)
        # Also record decisions for the admin UI.
        guard.add_observer(decision_log.record)
        with self._lock:
            self._guards[guard.config.name] = guard
            self._guard_files[guard.config.name] = path
            self._file_mtime[path] = path.stat().st_mtime
        log.info("Loaded guard '%s' from %s", guard.config.name, path)
        return True

    def load_from_dir(self, directory: str | Path) -> list[str]:
        directory = Path(directory)
        loaded: list[str] = []
        for yml in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
            if self._load_file(yml):
                # Find the guard name we just registered for this file
                with self._lock:
                    for name, p in self._guard_files.items():
                        if p == yml and name not in loaded:
                            loaded.append(name)
                            break
        return loaded

    def get(self, guard_id: str) -> DomainGuard | None:
        with self._lock:
            return self._guards.get(guard_id)

    def all_guards(self) -> list[DomainGuard]:
        with self._lock:
            return list(self._guards.values())

    def list_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._guards.keys())

    # ---- hot reload ----

    def start_watching(self, interval: float = 2.0) -> None:
        if self._watch_thread is not None:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._watch_loop, args=(interval,), daemon=True)
        t.start()
        self._watch_thread = t
        log.info("Hot reload watcher started (interval=%.1fs)", interval)

    def stop_watching(self) -> None:
        self._stop_event.set()

    def _watch_loop(self, interval: float) -> None:
        while not self._stop_event.is_set():
            time.sleep(interval)
            try:
                self._check_and_reload()
            except Exception as e:
                log.error("Watcher iteration failed: %s", e)

    def _check_and_reload(self) -> None:
        with self._lock:
            files = list(self._file_mtime.items())
        for path, known_mtime in files:
            if not path.exists():
                # File deleted → drop the guard
                with self._lock:
                    name = next(
                        (n for n, p in self._guard_files.items() if p == path), None
                    )
                    if name:
                        log.info("Removing guard '%s' (file deleted)", name)
                        self._guards.pop(name, None)
                        self._guard_files.pop(name, None)
                    self._file_mtime.pop(path, None)
                continue
            current_mtime = path.stat().st_mtime
            if current_mtime > known_mtime:
                log.info("Detected change in %s, reloading...", path)
                if self._load_file(path):
                    self.reload_count += 1


# ---------------- module-level singletons (set on startup) ----------------

metrics = MetricsCollector()
decision_log = DecisionLog(max_entries=int(os.environ.get("DECISION_LOG_SIZE", "500")))
registry: GuardRegistry | None = None
limiter: RateLimiter | None = None
router_singleton: GuardRouter | None = None
router_guards_signature: tuple[str, ...] = ()


# ---------------- FastAPI app ----------------

app = FastAPI(title="domain-guard sidecar", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    global registry, limiter
    cache_size = int(os.environ.get("CACHE_SIZE", "1024"))
    cache_ttl = float(os.environ.get("CACHE_TTL", "3600"))
    registry = GuardRegistry(
        cache_size=cache_size, cache_ttl=cache_ttl, metrics=metrics
    )

    guards_dir = os.environ.get("GUARDS_DIR", "./guards")
    if Path(guards_dir).is_dir():
        loaded = registry.load_from_dir(guards_dir)
        log.info("Sidecar ready with guards: %s", loaded)
    else:
        log.warning("GUARDS_DIR='%s' does not exist; no guards loaded.", guards_dir)

    if os.environ.get("HOT_RELOAD") == "1":
        registry.start_watching()

    # ---- rate limit ----
    rl_n = int(os.environ.get("RATE_LIMIT", "0"))
    if rl_n > 0:
        rl_window = float(os.environ.get("RATE_WINDOW", "60"))
        backend = os.environ.get("RATE_BACKEND", "memory")
        if backend == "redis":
            from .ratelimit import RedisRateLimiter
            limiter = RedisRateLimiter(
                url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                limit=rl_n,
                window_seconds=int(rl_window),
            )
        else:
            limiter = TokenBucketLimiter(
                capacity=rl_n,
                refill_per_sec=rl_n / rl_window,
            )
        log.info("Rate limiting enabled: %s req/%ss (backend=%s)",
                 rl_n, rl_window, backend)


@app.on_event("shutdown")
def _shutdown() -> None:
    if registry is not None:
        registry.stop_watching()


def _rate_limit_key(req: CheckRequest, request: Request) -> str:
    if req.user_id:
        return f"user:{req.user_id}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


@app.get("/health")
def health() -> dict:
    assert registry is not None
    return {
        "status": "ok",
        "guards": registry.list_ids(),
        "reload_count": registry.reload_count,
        "rate_limit": limiter.stats() if limiter else None,
        "auth": {
            "admin_required": admin_auth_enabled(),
            "check_required": check_auth_enabled(),
        },
    }


@app.get("/v1/guards")
def list_guards() -> dict:
    assert registry is not None
    return {"guards": registry.list_ids()}


@app.post("/v1/check", response_model=CheckResponse)
def check(req: CheckRequest, request: Request, debug: bool = False,
          _auth=Depends(require_check)) -> Response:
    assert registry is not None
    guard = registry.get(req.guard_id)
    if guard is None:
        raise HTTPException(404, f"Unknown guard_id: {req.guard_id}")

    # Rate limit BEFORE running the pipeline — that's the whole point.
    if limiter is not None:
        rl = limiter.take(_rate_limit_key(req, request))
        if not rl.allowed:
            return Response(
                content=(
                    '{"error":"rate_limited","limit":%d,"reset_in":%.1f}'
                    % (rl.limit, rl.reset_in)
                ),
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(int(rl.reset_in) + 1),
                    "X-RateLimit-Limit": str(rl.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    ctx = GuardContext(
        session_id=req.session_id,
        state=req.state,
        history=req.history,
        user_id=req.user_id,
        metadata=req.metadata or {},
    )
    result = guard.check(req.message, ctx)
    cache_hit = bool((result.debug or {}).get("cache_hit"))

    response_body = CheckResponse(
        passed=result.passed,
        matched_layer=result.matched_layer,
        confidence=result.confidence,
        reason=result.reason,
        fallback_reply=result.fallback_reply,
        suggested_replies=result.suggested_replies,
        latency_ms=result.latency_ms,
        cache_hit=cache_hit,
        debug=result.debug if debug else None,
    )
    # Wrap in a Response so we can attach rate-limit headers when present.
    import json as _json
    headers = {}
    if limiter is not None:
        # Cheap second peek to fill headers — for accurate remaining count
        # we'd track it on the original take(), but this is fine for UX.
        pass
    return Response(
        content=_json.dumps(response_body.model_dump(), ensure_ascii=False),
        media_type="application/json",
        headers=headers,
    )


@app.get("/metrics")
def prom_metrics() -> Response:
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)


@app.post("/v1/route", response_model=RouteResponse)
def route(req: RouteRequest, request: Request,
          _auth=Depends(require_check)) -> RouteResponse:
    global router_singleton, router_guards_signature
    assert registry is not None
    guards = registry.all_guards()
    if not guards:
        raise HTTPException(503, "no guards loaded")

    if limiter is not None:
        key = f"user:{req.user_id}" if req.user_id else (
            f"ip:{request.client.host if request.client else 'unknown'}"
        )
        rl = limiter.take(key)
        if not rl.allowed:
            raise HTTPException(429, detail={
                "error": "rate_limited", "limit": rl.limit, "reset_in": rl.reset_in,
            })

    # Rebuild router only when the set of loaded guards changes (e.g. hot reload).
    sig = tuple(g.config.name for g in guards)
    if router_singleton is None or router_guards_signature != sig:
        router_singleton = GuardRouter(guards)
        router_guards_signature = sig

    ctx = GuardContext(
        session_id=req.session_id, state=req.state, user_id=req.user_id
    )
    result = router_singleton.route(
        req.message, ctx, available_guards=req.available_guards
    )

    if result.passed:
        gr = result.guard_result
        assert gr is not None
        return RouteResponse(
            matched_guard=result.matched_guard,
            passed=True,
            matched_layer=gr.matched_layer,
            confidence=gr.confidence,
            reason=gr.reason,
            sticky_hit=result.sticky_hit,
        )

    # Nothing passed — return the strongest alternative's fallback reply.
    alts = [
        RouteAlternative(guard=name, confidence=gr.confidence, reason=gr.reason)
        for name, gr in result.alternatives
    ]
    top_reply = (result.alternatives[0][1].fallback_reply
                 if result.alternatives else
                 "Sorry, I can't help with that.")
    top_suggestions = (result.alternatives[0][1].suggested_replies
                       if result.alternatives else [])
    return RouteResponse(
        matched_guard=None,
        passed=False,
        fallback_reply=top_reply,
        suggested_replies=top_suggestions,
        alternatives=alts,
    )


# ---------------- admin endpoints ----------------

class UpdateConfigBody(BaseModel):
    content: str


@app.get("/v1/decisions/recent")
def recent_decisions(limit: int = 50, guard: str | None = None,
                     only_blocked: bool = False) -> dict:
    return {
        "items": decision_log.recent(limit=limit, guard=guard,
                                     only_blocked=only_blocked),
        "summary": decision_log.summary(),
    }


@app.get("/v1/guards/{guard_id}/config")
def get_guard_config(guard_id: str) -> dict:
    assert registry is not None
    with registry._lock:  # type: ignore[attr-defined]
        path = registry._guard_files.get(guard_id)
    if path is None or not path.exists():
        raise HTTPException(404, f"Unknown guard or missing file: {guard_id}")
    return {"guard_id": guard_id, "path": str(path),
            "content": path.read_text(encoding="utf-8")}


@app.put("/v1/guards/{guard_id}/config")
def put_guard_config(guard_id: str, body: UpdateConfigBody,
                     _auth=Depends(require_admin)) -> dict:
    assert registry is not None
    with registry._lock:  # type: ignore[attr-defined]
        path = registry._guard_files.get(guard_id)
    if path is None:
        raise HTTPException(404, f"Unknown guard: {guard_id}")

    # Validate by attempting a load into a throwaway guard first.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write(body.content)
        tmp_path = Path(tmp.name)
    try:
        try:
            DomainGuard.from_yaml(tmp_path)
        except Exception as e:
            raise HTTPException(400, f"Invalid config: {e}")
        # Write to the real path and force a reload via the registry helper.
        path.write_text(body.content, encoding="utf-8")
        ok = registry._load_file(path)  # type: ignore[attr-defined]
        if not ok:
            raise HTTPException(500, "Reload after write failed; see logs")
        registry.reload_count += 1
        return {"reloaded": True, "guard_id": guard_id,
                "reload_count": registry.reload_count}
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@app.post("/v1/guards/{guard_id}/reload")
def reload_guard(guard_id: str, _auth=Depends(require_admin)) -> dict:
    assert registry is not None
    with registry._lock:  # type: ignore[attr-defined]
        path = registry._guard_files.get(guard_id)
    if path is None:
        raise HTTPException(404, f"Unknown guard: {guard_id}")
    if not registry._load_file(path):  # type: ignore[attr-defined]
        raise HTTPException(500, "Reload failed")
    registry.reload_count += 1
    return {"reloaded": True, "reload_count": registry.reload_count}


@app.get("/admin")
def admin_page() -> Response:
    html = (Path(__file__).parent / "admin.html").read_text(encoding="utf-8")
    return Response(content=html, media_type="text/html")


# Allow `python -m domain_guard.sidecar`
def _main() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    _main()
