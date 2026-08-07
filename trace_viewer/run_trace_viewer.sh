#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HOST="${TRACE_VIEWER_HOST:-127.0.0.1}"
PORT="${TRACE_VIEWER_PORT:-30013}"
REPORTS_DIR="${TRACE_VIEWER_REPORTS:-$SCRIPT_DIR/../reports}"

exec python3 "$SCRIPT_DIR/server.py" \
  --host "$HOST" \
  --port "$PORT" \
  --reports "$REPORTS_DIR"

