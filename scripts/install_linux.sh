#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE:-}" != linux* ]]; then
  echo "This installer currently targets Linux only."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
SOURCE="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  SOURCE="$(readlink -f "$SOURCE")"
fi

SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

resolve_uv_bin() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi

  if [[ -n "${SUDO_USER:-}" ]]; then
    local user_home
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    if [[ -n "$user_home" && -x "$user_home/.local/bin/uv" ]]; then
      echo "$user_home/.local/bin/uv"
      return 0
    fi
  fi

  return 1
}

run_sync() {
  local uv_bin="$1"
  if [[ "${EUID}" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    local user_home
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    sudo -u "$SUDO_USER" env HOME="$user_home" PATH="$PATH:$user_home/.local/bin" "$uv_bin" sync
    return 0
  fi

  "$uv_bin" sync
}

UV_BIN="$(resolve_uv_bin || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

cd "$REPO_ROOT"
run_sync "$UV_BIN"

chmod +x "$REPO_ROOT/scripts/yimin"

INSTALL_ARGS=(--enable --start)

if [[ "${EUID}" -eq 0 ]]; then
  mkdir -p /usr/local/bin
  ln -sfn "$REPO_ROOT/scripts/yimin" /usr/local/bin/yimin
  INSTALL_ARGS+=(--scope system)
  if [[ -n "${SUDO_USER:-}" ]]; then
    INSTALL_ARGS+=(--service-user "$SUDO_USER")
  fi
else
  mkdir -p "$HOME/.local/bin"
  ln -sfn "$REPO_ROOT/scripts/yimin" "$HOME/.local/bin/yimin"
  if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "Add ~/.local/bin to PATH to use 'yimin' directly in new shells."
  fi
fi

"$REPO_ROOT/scripts/yimin" install "${INSTALL_ARGS[@]}" "$@"

echo
echo "Installed successfully."
if [[ "${EUID}" -eq 0 ]]; then
  echo "Use: sudo yimin status | sudo yimin restart | sudo yimin logs"
else
  echo "Use: yimin status | yimin restart | yimin logs"
fi
