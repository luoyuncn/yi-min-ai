"""`yimin` Linux 生命周期管理命令。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent.deploy.linux import (
    DEFAULT_SERVICE_NAME,
    SYSTEM_SCOPE,
    USER_SCOPE,
    build_daemon_reload_command,
    build_journalctl_command,
    build_systemctl_command,
    default_data_root,
    default_scope,
    default_service_path,
    render_system_service,
    render_user_service,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_linux() -> None:
    if sys.platform != "linux":
        raise SystemExit("`yimin` lifecycle commands currently support Linux only.")


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _effective_scope(scope: str | None) -> str:
    return scope or default_scope()


def _resolve_service_identity(args: argparse.Namespace, scope: str) -> tuple[str | None, str | None]:
    if scope != SYSTEM_SCOPE:
        return None, None

    service_user = args.service_user or os.environ.get("SUDO_USER")
    if not service_user:
        raise SystemExit("System-scope install requires --service-user, or run it via sudo so SUDO_USER is available.")
    service_group = args.service_group or service_user
    return service_user, service_group


def _write_service_file(
    service_name: str,
    data_root: Path,
    *,
    scope: str,
    service_user: str | None = None,
    service_group: str | None = None,
) -> Path:
    repo_root = _repo_root()
    service_path = default_service_path(service_name=service_name, scope=scope)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    if scope == SYSTEM_SCOPE:
        if service_user is None:
            raise SystemExit("System-scope service install requires a service user.")
        shutil.chown(data_root, user=service_user, group=service_group or service_user)
        rendered = render_system_service(
            repo_root=repo_root,
            data_root=data_root,
            service_user=service_user,
            service_group=service_group,
            config_path=repo_root / "config" / "agent.linux.yaml",
            service_name=service_name,
        )
    else:
        rendered = render_user_service(
            repo_root=repo_root,
            data_root=data_root,
            config_path=repo_root / "config" / "agent.linux.yaml",
            service_name=service_name,
        )
    service_path.write_text(rendered, encoding="utf-8")
    return service_path


def _cmd_install(args: argparse.Namespace) -> None:
    _require_linux()
    scope = _effective_scope(args.scope)
    data_root = (args.data_root or default_data_root(scope=scope)).resolve()
    service_user, service_group = _resolve_service_identity(args, scope)

    service_path = _write_service_file(
        args.service_name,
        data_root,
        scope=scope,
        service_user=service_user,
        service_group=service_group,
    )
    _run(build_daemon_reload_command(scope=scope))
    if args.enable:
        _run(build_systemctl_command("enable", args.service_name, scope=scope))
    if args.start:
        _run(build_systemctl_command("start", args.service_name, scope=scope))

    print(f"Service installed: {service_path}")
    print(f"Scope: {scope}")
    print(f"Data root: {data_root}")
    if scope == SYSTEM_SCOPE:
        print(f"Service user: {service_user}")
    else:
        print("Tip: run `loginctl enable-linger $USER` if you want the user service to survive logout.")


def _cmd_uninstall(args: argparse.Namespace) -> None:
    _require_linux()
    scope = _effective_scope(args.scope)

    service_path = default_service_path(service_name=args.service_name, scope=scope)
    if service_path.exists():
        try:
            _run(build_systemctl_command("stop", args.service_name, scope=scope))
        except subprocess.CalledProcessError:
            pass
        try:
            _run(build_systemctl_command("disable", args.service_name, scope=scope))
        except subprocess.CalledProcessError:
            pass
        service_path.unlink()
        _run(build_daemon_reload_command(scope=scope))
        print(f"Removed service file: {service_path}")
    else:
        print(f"Service file not found: {service_path}")


def _cmd_service_action(args: argparse.Namespace) -> None:
    _require_linux()
    _run(build_systemctl_command(args.command, args.service_name, scope=_effective_scope(args.scope)))


def _cmd_logs(args: argparse.Namespace) -> None:
    _require_linux()
    _run(
        build_journalctl_command(
            follow=args.follow,
            lines=args.lines,
            service_name=args.service_name,
            scope=_effective_scope(args.scope),
        )
    )


def _add_scope_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        choices=[USER_SCOPE, SYSTEM_SCOPE],
        default=None,
        help="Service scope. Defaults to user for normal users, system when running as root.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yimin", description="Yi Min AI Linux lifecycle manager")
    parser.set_defaults(command=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a systemd service for Yi Min")
    _add_scope_argument(install)
    install.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="External runtime data directory. Defaults depend on scope.",
    )
    install.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    install.add_argument("--service-user", help="System-scope only: Unix user that should run the service.")
    install.add_argument("--service-group", help="System-scope only: Unix group for the service user.")
    install.add_argument("--enable", action=argparse.BooleanOptionalAction, default=True)
    install.add_argument("--start", action="store_true", default=False)
    install.set_defaults(handler=_cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="Remove the installed systemd service")
    _add_scope_argument(uninstall)
    uninstall.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    uninstall.set_defaults(handler=_cmd_uninstall)

    for action in ("start", "stop", "restart", "status"):
        sub = subparsers.add_parser(action, help=f"{action.capitalize()} the service")
        _add_scope_argument(sub)
        sub.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
        sub.set_defaults(handler=_cmd_service_action)

    logs = subparsers.add_parser("logs", help="Show service logs")
    _add_scope_argument(logs)
    logs.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    logs.add_argument("-f", "--follow", action="store_true", default=False)
    logs.add_argument("-n", "--lines", type=int, default=None)
    logs.set_defaults(handler=_cmd_logs)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
