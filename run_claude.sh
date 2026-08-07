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

PROXY_HOST="${CLAUDE_PROXY_HOST:-127.0.0.1}"
PROXY_PORT="${CLAUDE_PROXY_PORT:-30012}"
PROXY_URL="${CLAUDE_PROXY_URL:-http://$PROXY_HOST:$PROXY_PORT}"
HEALTH_URL="$PROXY_URL/healthz"
PROXY_LOG="${CLAUDE_PROXY_LOG:-$SCRIPT_DIR/proxy.log}"

if ! command -v claude >/dev/null 2>&1; then
  printf 'claude command not found in WSL PATH.\n' >&2
  exit 127
fi
if ! command -v curl >/dev/null 2>&1; then
  printf 'curl is required to check the proxy health.\n' >&2
  exit 127
fi
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
  printf 'uv is required to provide the proxy dependencies.\n' >&2
  exit 127
fi

proxy_pid=""
proxy_started=0
cleanup() {
  if [[ "$proxy_started" -eq 1 && -n "$proxy_pid" ]]; then
    kill -- "-$proxy_pid" 2>/dev/null || kill "$proxy_pid" 2>/dev/null || true
    wait "$proxy_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  setsid "$UV_BIN" run \
    --with fastapi \
    --with httpx \
    --with uvicorn \
    python "$SCRIPT_DIR/claude_proxy.py" \
    --host "$PROXY_HOST" \
    --port "$PROXY_PORT" \
    --upstream "$UPSTREAM_URL" \
    --direct \
    --report \
    --log-level "${CLAUDE_PROXY_LOG_LEVEL:-info}" \
    >"$PROXY_LOG" 2>&1 &
  proxy_pid=$!
  proxy_started=1
fi

ready=0
for _ in {1..50}; do
  if curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if [[ "$proxy_started" -eq 1 ]] && ! kill -0 "$proxy_pid" 2>/dev/null; then
    printf 'Proxy exited before becoming healthy. Log: %s\n' "$PROXY_LOG" >&2
    tail -n 40 "$PROXY_LOG" >&2 || true
    exit 1
  fi
  sleep 0.1
done

if [[ "$ready" -ne 1 ]]; then
  printf 'Proxy did not become healthy at %s. Log: %s\n' "$HEALTH_URL" "$PROXY_LOG" >&2
  exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "${API_KEY:-}" ]]; then
  export ANTHROPIC_API_KEY="$API_KEY"
fi
if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}" && -n "${API_KEY:-}" ]]; then
  export ANTHROPIC_AUTH_TOKEN="$API_KEY"
fi
if [[ -n "${MODEL:-}" && -z "${ANTHROPIC_MODEL:-}" ]]; then
  export ANTHROPIC_MODEL="$MODEL"
fi
export ANTHROPIC_BASE_URL="$PROXY_URL"


claude "$@"

