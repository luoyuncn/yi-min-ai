#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE:-}" != linux* ]]; then
  echo "This installer currently targets Linux only."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  if [[ -z "${SUDO_USER:-}" ]]; then
    echo "uv is required. Install it first: https://docs.astral.sh/uv/"
    exit 1
  fi
fi

SOURCE="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then
  SOURCE="$(readlink -f "$SOURCE")"
fi

SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

append_env_if_set() {
  local target_name="$1"
  local source_name="${2:-$1}"
  local source_value="${!source_name:-}"
  if [[ -n "$source_value" ]]; then
    UV_SYNC_ENV+=("$target_name=$source_value")
  fi
}

build_uv_sync_env() {
  UV_SYNC_ENV=()
  append_env_if_set UV_DEFAULT_INDEX
  append_env_if_set UV_DEFAULT_INDEX YIMIN_UV_DEFAULT_INDEX
  append_env_if_set UV_INDEX
  append_env_if_set UV_INDEX YIMIN_UV_INDEX
  append_env_if_set UV_INDEX_URL
  append_env_if_set UV_EXTRA_INDEX_URL
  append_env_if_set UV_FIND_LINKS
  append_env_if_set UV_INDEX_STRATEGY
  append_env_if_set UV_NATIVE_TLS
  append_env_if_set UV_NATIVE_TLS YIMIN_UV_NATIVE_TLS
  append_env_if_set UV_CACHE_DIR
}

print_uv_sync_failure_help() {
  echo
  echo "uv sync failed while downloading Python dependencies."
  echo "This usually means the current Linux host cannot reliably reach PyPI/CDN."
  echo
  echo "Try rerunning with a mirror:"
  echo "  sudo UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh"
  echo
  echo "If your network requires the system certificate store, also try:"
  echo "  sudo UV_NATIVE_TLS=true UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh"
  echo
  echo "You can also set script-specific aliases:"
  echo "  sudo YIMIN_UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh"
  echo "  sudo YIMIN_UV_NATIVE_TLS=true ./scripts/install_linux.sh"
}

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

repair_uv_cache_permissions() {
  if [[ "${EUID}" -ne 0 || -z "${SUDO_USER:-}" ]]; then
    return 0
  fi

  local user_home user_group
  user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  user_group="$(id -gn "$SUDO_USER")"

  mkdir -p "$user_home/.cache/uv"
  chown "$SUDO_USER:$user_group" "$user_home/.cache" "$user_home/.cache/uv"
  chown -R "$SUDO_USER:$user_group" "$user_home/.cache/uv"
}

run_sync() {
  local uv_bin="$1"
  build_uv_sync_env
  if [[ "${EUID}" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    local user_home
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    repair_uv_cache_permissions
    if ! sudo -u "$SUDO_USER" env HOME="$user_home" PATH="$PATH:$user_home/.local/bin" "${UV_SYNC_ENV[@]}" "$uv_bin" sync; then
      print_uv_sync_failure_help
      return 1
    fi
    return 0
  fi

  if ! env "${UV_SYNC_ENV[@]}" "$uv_bin" sync; then
    print_uv_sync_failure_help
    return 1
  fi
}

UV_BIN="$(resolve_uv_bin || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

cd "$REPO_ROOT"
run_sync "$UV_BIN"

chmod +x "$REPO_ROOT/scripts/yimin"
chmod +x "$REPO_ROOT/scripts/run_linux_service.sh"

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
