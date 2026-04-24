"""Linux systemd 生命周期管理相关函数。"""

from pathlib import Path

DEFAULT_SERVICE_NAME = "yimin"


def default_data_root(home: Path | None = None) -> Path:
    """返回用户态部署默认使用的数据根目录。"""

    base = home or Path.home()
    return (base / ".local" / "share" / "yi-min-ai").resolve()


def default_service_dir(home: Path | None = None) -> Path:
    """返回用户态 systemd service 目录。"""

    base = home or Path.home()
    return (base / ".config" / "systemd" / "user").resolve()


def default_service_path(home: Path | None = None, service_name: str = DEFAULT_SERVICE_NAME) -> Path:
    """返回用户态 systemd service 文件路径。"""

    return default_service_dir(home) / f"{service_name}.service"


def render_user_service(
    *,
    repo_root: Path,
    data_root: Path,
    config_path: Path | None = None,
    python_executable: Path | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> str:
    """渲染用户态 systemd service 文件内容。"""

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    config_path = (config_path or repo_root / "config" / "agent.linux.yaml").resolve()
    python_executable = (python_executable or repo_root / ".venv" / "bin" / "python").resolve()

    return "\n".join(
        [
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
            (
                f"ExecStart={python_executable} -m agent.main --config {config_path}"
            ),
            "Restart=always",
            "RestartSec=5",
            "KillSignal=SIGINT",
            "TimeoutStopSec=20",
            f"SyslogIdentifier={service_name}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def build_systemctl_command(action: str, service_name: str = DEFAULT_SERVICE_NAME) -> list[str]:
    """构建 `systemctl --user` 命令。"""

    command = ["systemctl", "--user", action, service_name]
    if action == "status":
        command.append("--no-pager")
    return command


def build_journalctl_command(
    *,
    follow: bool = False,
    lines: int | None = None,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> list[str]:
    """构建查看用户态 service 日志的命令。"""

    command = ["journalctl", "--user", "-u", service_name]
    if lines is not None:
        command.extend(["-n", str(lines)])
    if follow:
        command.append("-f")
    return command
