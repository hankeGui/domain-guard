#!/usr/bin/env bash
# One-shot launcher for the domain-guard demo.
#
# Usage:
#   cp .env.example .env       # fill in your API key (or leave blank for mock)
#   ./run.sh

set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

# ---- venv ----
if [[ ! -d ".venv" ]]; then
  echo "[setup] Creating virtualenv in demo/.venv ..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# ---- deps ----
# Install the parent project in editable mode so the demo picks up local changes,
# plus the runtime extras the demo needs.
echo "[setup] Installing dependencies ..."
pip install --quiet --upgrade pip
pip install --quiet -e "$ROOT[local]" fastapi uvicorn python-dotenv anthropic openai

# ---- env ----
if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Tell domain-guard to use the hash embedder by default — it's instant and
# doesn't need to download the ~120MB sentence-transformers model on first run.
# Comment this line out if you want the real embedder (recommended for production).
export DOMAIN_GUARD_EMBEDDING="${DOMAIN_GUARD_EMBEDDING:-hash}"

PORT="${DEMO_PORT:-9000}"

# ---- which provider? ----
if [[ -n "${LLM_PROVIDER:-}" ]]; then
  PROVIDER_INFO="forced: $LLM_PROVIDER"
elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  PROVIDER_INFO="claude (ANTHROPIC_API_KEY detected)"
elif [[ -n "${OPENAI_API_KEY:-}" ]]; then
  PROVIDER_INFO="openai (OPENAI_API_KEY detected)"
else
  PROVIDER_INFO="mock (no API key — canned responses)"
fi

cat <<EOF

────────────────────────────────────────────
 domain-guard demo · 电商客服 agent
────────────────────────────────────────────
 LLM provider:  $PROVIDER_INFO
 Embedding:     $DOMAIN_GUARD_EMBEDDING
 URL:           http://localhost:$PORT
────────────────────────────────────────────

EOF

# Try to open the browser, but don't fail if we can't.
if command -v open >/dev/null 2>&1; then
  ( sleep 1 && open "http://localhost:$PORT" ) &
elif command -v xdg-open >/dev/null 2>&1; then
  ( sleep 1 && xdg-open "http://localhost:$PORT" ) &
fi

exec python -m shop_agent.server
