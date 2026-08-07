#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${CLAUDE_PROXY_ENV_FILE:-$SCRIPT_DIR/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

UPSTREAM_URL="${CLAUDE_PROXY_UPSTREAM:-${EXTERN_BASE_URL:-${ANTHROPIC_BASE_URL:-}}}"
if [[ -z "$UPSTREAM_URL" ]]; then
  printf 'Missing upstream URL. Set ANTHROPIC_BASE_URL or CLAUDE_PROXY_UPSTREAM in %s.\n' "$ENV_FILE" >&2
  exit 2
fi
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
  printf 'uv is required to provide the proxy dependencies.\n' >&2
  exit 127
fi

exec "$UV_BIN" run \
  --with fastapi \
  --with httpx \
  --with uvicorn \
  python "$SCRIPT_DIR/claude_proxy.py" \
  --host "${CLAUDE_PROXY_HOST:-127.0.0.1}" \
  --port "${CLAUDE_PROXY_PORT:-30012}" \
  --upstream "$UPSTREAM_URL" \
  --direct \
  --report \
  --log-level "${CLAUDE_PROXY_LOG_LEVEL:-info}"

