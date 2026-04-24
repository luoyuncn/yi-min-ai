"""Linux systemd 生命周期管理相关函数。"""

import os
from pathlib import Path

DEFAULT_SERVICE_NAME = "yimin"
USER_SCOPE = "user"
SYSTEM_SCOPE = "system"


def default_scope(*, euid: int | None = None) -> str:
    """根据当前权限推断默认 scope。"""

    effective_uid = euid
    if effective_uid is None and hasattr(os, "geteuid"):
        effective_uid = os.geteuid()
    return SYSTEM_SCOPE if effective_uid == 0 else USER_SCOPE


def _normalize_scope(scope: str) -> str:
    if scope not in {USER_SCOPE, SYSTEM_SCOPE}:
        raise ValueError(f"Unsupported scope: {scope}")
    return scope


def default_data_root(*, scope: str = USER_SCOPE, home: Path | None = None) -> Path:
    """返回指定 scope 的默认数据根目录。"""

    scope = _normalize_scope(scope)
    if scope == SYSTEM_SCOPE:
        return Path("/var/lib/yi-min-ai")

    base = home or Path.home()
    return (base / ".local" / "share" / "yi-min-ai").resolve()


def default_service_dir(*, scope: str = USER_SCOPE, home: Path | None = None) -> Path:
    """返回指定 scope 的 systemd service 目录。"""

    scope = _normalize_scope(scope)
    if scope == SYSTEM_SCOPE:
        return Path("/etc/systemd/system")

    base = home or Path.home()
    return (base / ".config" / "systemd" / "user").resolve()


def default_service_path(
    *,
    home: Path | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
    scope: str = USER_SCOPE,
) -> Path:
    """返回指定 scope 的 service 文件路径。"""

    return default_service_dir(scope=scope, home=home) / f"{service_name}.service"


def _render_service(
    *,
    repo_root: Path,
    data_root: Path,
    config_path: Path | None,
    python_executable: Path | None,
    service_name: str,
    wanted_by: str,
    service_user: str | None = None,
    service_group: str | None = None,
) -> str:
    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    config_path = (config_path or repo_root / "config" / "agent.linux.yaml").resolve()
    launcher_script = (repo_root / "scripts" / "run_linux_service.sh").resolve()

    if python_executable is not None:
        python_executable = python_executable.resolve()
        exec_start = f"{python_executable} -m agent.main --config {config_path}"
    else:
        exec_start = f"{launcher_script} {config_path}"

    lines = [
        "[Unit]",
        f"Description=Yi Min AI Gateway ({service_name})",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={repo_root}",
        f"Environment=YIMIN_DATA_ROOT={data_root}",
        f"EnvironmentFile=-{repo_root / '.env'}",
        f"ExecStart={exec_start}",
        "Restart=always",
        "RestartSec=5",
        "KillSignal=SIGINT",
        "TimeoutStopSec=20",
        f"SyslogIdentifier={service_name}",
    ]

    if service_user:
        lines.append(f"User={service_user}")
    if service_group:
        lines.append(f"Group={service_group}")

    lines.extend(
        [
            "",
            "[Install]",
            f"WantedBy={wanted_by}",
            "",
        ]
    )
    return "\n".join(lines)


def render_user_service(
    *,
    repo_root: Path,
    data_root: Path,
    config_path: Path | None = None,
    python_executable: Path | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> str:
    """渲染用户态 systemd service 文件内容。"""

    return _render_service(
        repo_root=repo_root,
        data_root=data_root,
        config_path=config_path,
        python_executable=python_executable,
        service_name=service_name,
        wanted_by="default.target",
    )


def render_system_service(
    *,
    repo_root: Path,
    data_root: Path,
    service_user: str,
    service_group: str | None = None,
    config_path: Path | None = None,
    python_executable: Path | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> str:
    """渲染 system 级 service 文件内容。"""

    return _render_service(
        repo_root=repo_root,
        data_root=data_root,
        config_path=config_path,
        python_executable=python_executable,
        service_name=service_name,
        wanted_by="multi-user.target",
        service_user=service_user,
        service_group=service_group or service_user,
    )


def build_systemctl_command(
    action: str,
    service_name: str = DEFAULT_SERVICE_NAME,
    *,
    scope: str = USER_SCOPE,
) -> list[str]:
    """构建 systemctl 命令。"""

    scope = _normalize_scope(scope)
    command = ["systemctl"]
    if scope == USER_SCOPE:
        command.append("--user")
    command.extend([action, service_name])
    if action == "status":
        command.append("--no-pager")
    return command


def build_daemon_reload_command(*, scope: str = USER_SCOPE) -> list[str]:
    """构建 systemd daemon-reload 命令。"""

    scope = _normalize_scope(scope)
    command = ["systemctl"]
    if scope == USER_SCOPE:
        command.append("--user")
    command.append("daemon-reload")
    return command


def build_journalctl_command(
    *,
    follow: bool = False,
    lines: int | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
    scope: str = USER_SCOPE,
) -> list[str]:
    """构建查看 service 日志的命令。"""

    scope = _normalize_scope(scope)
    command = ["journalctl"]
    if scope == USER_SCOPE:
        command.append("--user")
    command.extend(["-u", service_name])
    if lines is not None:
        command.extend(["-n", str(lines)])
    if follow:
        command.append("-f")
    return command
