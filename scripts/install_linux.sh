#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE:-}" != linux* ]]; then
  echo "This installer currently targets Linux only."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

SOURCE="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  SOURCE="$(readlink -f "$SOURCE")"
fi

SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
uv sync

mkdir -p "$HOME/.local/bin"
chmod +x "$REPO_ROOT/scripts/yimin"
ln -sfn "$REPO_ROOT/scripts/yimin" "$HOME/.local/bin/yimin"

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  echo "Add ~/.local/bin to PATH to use 'yimin' directly in new shells."
fi

"$REPO_ROOT/scripts/yimin" install --enable --start "$@"

echo
echo "Installed successfully."
echo "Use: yimin status | yimin restart | yimin logs"
