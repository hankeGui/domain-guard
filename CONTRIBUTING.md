# Contributing

Thanks for considering a contribution. The project is small enough that the path is short.

## Setup

```bash
git clone https://github.com/hankeGui/domain-guard.git
cd domain-guard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]" pytest pytest-timeout
```

## Run the tests

```bash
pytest tests/unit         # fast (< 1s)
pytest tests/             # everything, including subprocess sidecar tests (~1 min)
```

The hash embedding fallback is forced on in tests, so no network or model download is needed.

## Code style

- No formal linter is enforced yet; match the existing style (PEP 8-ish, type hints on the public API).
- Public functions should have one-line docstrings explaining intent.
- Don't add files for the sake of organization — keep modules cohesive.
- No emoji in code, comments, or YAML.

## Commit messages

Imperative mood, short. Examples:
- `Add llm_fallback layer config docs`
- `Fix off-by-one in cache LRU eviction`
- `Drop hot_reload watcher when GUARDS_DIR is empty`

## Pull requests

- Each PR should be one logical change.
- Tests should pass and ideally cover the new behavior.
- For new pipeline layers, include both a unit test and an example YAML snippet.
- For breaking config changes, bump the version and note it in `CHANGELOG.md` under "Breaking changes."

## Where to ask questions

Open a discussion or an issue on GitHub. For design questions, sketch the change in a comment before writing it — the maintainer is happy to push back early rather than late.

## Adding a feature

If you're adding something user-visible:

1. Code + unit test
2. `docs/user-guide.md` section
3. `CHANGELOG.md` entry
4. If it affects the AI workflow, `docs/ai-dev.md` note

Skip step 4 for internal-only refactors.

## Reporting a bug

Include:
- Python version, OS
- Minimum YAML + Python snippet that reproduces it
- What you expected vs what happened

Bonus points for a failing test in `tests/`.

## License

By contributing, you agree your contribution is licensed under the project's MIT license.
