#!/usr/bin/env bash
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  SOURCE="$(readlink -f "$SOURCE")"
fi

SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${1:-$REPO_ROOT/config/agent.linux.yaml}"

cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  exec "$REPO_ROOT/.venv/bin/python" -m agent.main --config "$CONFIG_PATH"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run python -m agent.main --config "$CONFIG_PATH"
fi

echo "Unable to start Yi Min service: no runnable Python found. Run 'uv sync' in $REPO_ROOT first." >&2
exit 1
