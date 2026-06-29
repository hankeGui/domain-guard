# AI Agent Development Guide

A guide for AI coding agents (Claude Code, Copilot, Cursor, etc.) working inside this repo. Read this first — it'll save you and the human reviewer time.

## What this project is

A **library + sidecar** that screens off-topic messages out of an LLM agent's request stream. Off-topic = "this message isn't about what this agent is supposed to handle." Catching them early means we don't burn tokens running a million-parameter model to say "I can't help with that."

It is **not** an LLM safety classifier, a content moderator, or a prompt firewall. Those problems are adjacent but separate.

## The 30-second mental model

```
user message
   ↓
DomainGuard.check(message, context)
   ↓
[ context_bypass → rule → embedding → llm_fallback ]
   ↓
each layer returns "pass" | "block" | "defer"
first non-defer wins; all-defer → block (fail-closed)
   ↓
GuardResult(passed, matched_layer, confidence, reason, fallback_reply, ...)
```

If you understand that, you understand 80% of the codebase.

## Files you'll most likely touch

| Task | File |
|------|------|
| "Add a regex/keyword to block X" | `examples/<guard>.yaml` |
| "Add a new pipeline layer type" | `domain_guard/layers/<new>.py` + register in `layers/__init__.py` |
| "Add a new HTTP endpoint" | `domain_guard/sidecar.py` |
| "Tweak the admin UI" | `domain_guard/admin.html` (vanilla JS, no build step) |
| "Change result fields" | `domain_guard/context.py` (then chase callers) |
| "Cheaper / different embedding" | `domain_guard/providers/embedding_*.py` |
| "Different rate-limit algorithm" | `domain_guard/ratelimit.py` |
| "New CLI subcommand" | `domain_guard/<name>.py` + wire from `cli.py` |

## Files you should rarely touch

- `domain_guard/core.py` — the pipeline driver. If a feature can be expressed as a new Layer or a new Observer, do that instead.
- `domain_guard/context.py` — `GuardContext` / `GuardResult` are public API. Changes are breaking.
- `pyproject.toml` — only when adding a real dependency.

## Conventions an AI should know

### YAML is the user-facing API surface

If you can express something as a YAML field, do that instead of a flag in Python. Users edit YAMLs in production via the admin UI; they don't redeploy Python code.

### "Fail closed" is non-negotiable

If a pipeline can't decide, the answer is **block**. Never default to pass. Tests in `tests/unit/test_core.py::TestFailClosed` lock this in.

### Layers are pure

Don't reach into `self.providers` to read external state at decide-time. Read everything you need in `setup()`.

### Tests live in `tests/`, not in `examples/`

There used to be `examples/test_*.py`. Those are gone — `tests/unit/` and `tests/integration/` is the home now. `examples/` is for runnable demos and YAML samples.

### One concept per file

- `cache.py` only knows about caching, not rate limiting.
- `metrics.py` doesn't know about the decision log.
- Layers don't know about each other.

If you find yourself crossing these lines, that's a smell — the abstraction probably wants to grow first.

### No emoji in committed code or YAML

(Per the user's preference. This rule sticks.)

### No backwards-compatibility scaffolding

If you change something, change it. Don't add `# deprecated, keeping for backwards compat` lines. The project is pre-1.0; we move fast.

## How to make changes that don't break things

1. **Always run the test suite.** `pytest tests/unit` for fast feedback (< 1 s); `pytest tests/` before claiming "done."
2. **The hash embedder is the test default.** `DOMAIN_GUARD_EMBEDDING=hash` is set in `tests/conftest.py` — don't change that unless you know what you're doing.
3. **Sidecar tests spawn a subprocess.** They're slow but they're the truth. Don't replace them with mocked tests.
4. **The admin UI is served from disk on every request.** Edit `admin.html` and just refresh the browser — no restart needed.
5. **Hot reload picks up YAML changes every ~2 s.** If you're testing reload behavior, give it time.

## Common tasks — task-shaped prompts

These are the kinds of asks an AI agent should be ready to handle. Each one points to where the work lands.

### "Add a new block pattern for X"

→ Edit `examples/forecast-agent.yaml` (or whatever guard). No code changes. Run the unit tests to make sure nothing else broke.

### "Add a new pipeline layer that does Y"

→ Read `docs/dev.md#adding-a-new-pipeline-layer` — it has a working template. Steps: subclass `Layer`, register in `layers/__init__.py`, add a unit test, document it in `docs/user-guide.md#the-four-pipeline-layers`.

### "Add a new sidecar endpoint"

→ Add to `domain_guard/sidecar.py`. Pattern: declare a Pydantic `BaseModel`, add a `@app.post(...)`, write an integration test in `tests/integration/test_sidecar.py` using the `sidecar` fixture.

### "Make the admin UI show Z"

→ `admin.html` is vanilla JS. There's no build step. Add markup to the `<div id="tab-overview">` or `tab-config` section, fetch from an existing endpoint (or add a new one). Don't introduce a framework.

### "Speed up X"

→ Run the integration tests first to get a baseline (latency is in the histogram). Profile with `cProfile` if it's the library, or with `py-spy` if it's the sidecar. Most opportunities live in: avoiding embedding calls (cache), avoiding LLM fallback (better embedding examples), or trimming Pydantic validation overhead.

### "Document X"

→ Three audiences:
- `docs/user-guide.md`: someone trying to use it. Be concrete.
- `docs/dev.md`: someone trying to extend it. Show patterns.
- `docs/ai-dev.md` (this file): an AI agent. Cite file paths and field names.

## Anti-patterns to avoid

- **Don't add a "manager" or "factory" class** because something feels too direct. The codebase is deliberately flat.
- **Don't write defensive code for impossible inputs.** Pydantic handles request validation; layers can assume their config was parsed by `GuardConfig`.
- **Don't introduce a new module for one function.** Find an existing module where it belongs.
- **Don't merge layers.** "rule" and "embedding" stay separate even though both filter strings — that separation is the whole pipeline metaphor.
- **Don't add `from __future__ import annotations` if the file already has it.** Most do. Check before duplicating.
- **Don't add typing for typing's sake.** Use it where it documents the API (`def check(self, message: str, ...) -> GuardResult`), not for every local variable.

## Reading the codebase

Suggested order, in increasing depth:

1. `examples/forecast-agent.yaml` — see what a guard *looks like*
2. `domain_guard/__init__.py` and `context.py` — see what's exported
3. `domain_guard/core.py::DomainGuard.check` — the heart of the library
4. `domain_guard/layers/rule.py` — simplest concrete layer, ~40 lines
5. `domain_guard/sidecar.py` — where everything composes for HTTP
6. `tests/unit/test_core.py` — what behaviors are pinned down

## Asking the human good questions

Before changing anything non-trivial:

- "Should this be a new layer, or extend an existing one?"
- "Should this go in the sidecar or stay library-only?"
- "Is breaking the YAML schema acceptable, or do we need a migration?"

The user is the architect; the agent is the carpenter. Don't redesign without checking.

## When in doubt

- Run `pytest tests/unit/ -q` — it's a 0.5 s sanity check.
- Read the existing test for the file you're editing. Tests are the most accurate spec.
- Skim `docs/dev.md#design-principles`. Re-aligning with those resolves most "is this the right approach?" doubts.
