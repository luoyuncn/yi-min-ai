"""`yimin` Linux 生命周期管理命令。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from agent.deploy.linux import (
    DEFAULT_SERVICE_NAME,
    build_journalctl_command,
    build_systemctl_command,
    default_data_root,
    default_service_path,
    render_user_service,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_linux() -> None:
    if sys.platform != "linux":
        raise SystemExit("`yimin` lifecycle commands currently support Linux only.")


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _write_service_file(service_name: str, data_root: Path) -> Path:
    repo_root = _repo_root()
    service_path = default_service_path(service_name=service_name)
    service_path.parent.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        render_user_service(
            repo_root=repo_root,
            data_root=data_root,
            config_path=repo_root / "config" / "agent.linux.yaml",
            service_name=service_name,
        ),
        encoding="utf-8",
    )
    return service_path


def _cmd_install(args: argparse.Namespace) -> None:
    _require_linux()

    service_path = _write_service_file(args.service_name, args.data_root.resolve())
    _run(["systemctl", "--user", "daemon-reload"])
    if args.enable:
        _run(build_systemctl_command("enable", args.service_name))
    if args.start:
        _run(build_systemctl_command("start", args.service_name))

    print(f"Service installed: {service_path}")
    print(f"Data root: {args.data_root.resolve()}")
    print("Tip: run `loginctl enable-linger $USER` if you want the user service to survive logout.")


def _cmd_uninstall(args: argparse.Namespace) -> None:
    _require_linux()

    service_path = default_service_path(service_name=args.service_name)
    if service_path.exists():
        try:
            _run(build_systemctl_command("stop", args.service_name))
        except subprocess.CalledProcessError:
            pass
        try:
            _run(build_systemctl_command("disable", args.service_name))
        except subprocess.CalledProcessError:
            pass
        service_path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
        print(f"Removed service file: {service_path}")
    else:
        print(f"Service file not found: {service_path}")


def _cmd_service_action(args: argparse.Namespace) -> None:
    _require_linux()
    _run(build_systemctl_command(args.command, args.service_name))


def _cmd_logs(args: argparse.Namespace) -> None:
    _require_linux()
    _run(build_journalctl_command(follow=args.follow, lines=args.lines, service_name=args.service_name))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yimin", description="Yi Min AI Linux lifecycle manager")
    parser.set_defaults(command=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install the user-level systemd service")
    install.add_argument(
        "--data-root",
        type=Path,
        default=default_data_root(),
        help="External runtime data directory (default: ~/.local/share/yi-min-ai)",
    )
    install.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    install.add_argument("--enable", action=argparse.BooleanOptionalAction, default=True)
    install.add_argument("--start", action="store_true", default=False)
    install.set_defaults(handler=_cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="Remove the user-level systemd service")
    uninstall.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    uninstall.set_defaults(handler=_cmd_uninstall)

    for action in ("start", "stop", "restart", "status"):
        sub = subparsers.add_parser(action, help=f"{action.capitalize()} the service")
        sub.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
        sub.set_defaults(handler=_cmd_service_action)

    logs = subparsers.add_parser("logs", help="Show service logs")
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
