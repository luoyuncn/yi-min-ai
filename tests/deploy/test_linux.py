import shutil
import subprocess
from pathlib import Path

import pytest

from agent.deploy.linux import (
    build_journalctl_command,
    build_systemctl_command,
    render_system_service,
    render_user_service,
)


def test_render_user_service_points_to_repo_venv_and_external_runtime_dir(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    data_root = tmp_path / "state"
    repo_root.mkdir()

    rendered = render_user_service(
        repo_root=repo_root,
        data_root=data_root,
        config_path=repo_root / "config" / "agent.linux.yaml",
    )

    assert f"WorkingDirectory={repo_root}" in rendered
    assert f"Environment=YIMIN_DATA_ROOT={data_root}" in rendered
    assert (
        f"ExecStart={repo_root / '.venv' / 'bin' / 'python'} -m agent.main --config "
        f"{repo_root / 'config' / 'agent.linux.yaml'}"
    ) in rendered


def test_systemd_commands_use_user_scope_and_service_name() -> None:
    assert build_systemctl_command("restart") == ["systemctl", "--user", "restart", "yimin"]
    assert build_systemctl_command("status") == ["systemctl", "--user", "status", "yimin", "--no-pager"]
    assert build_journalctl_command(follow=True) == ["journalctl", "--user", "-u", "yimin", "-f"]


def test_render_system_service_runs_as_target_user_and_targets_multi_user(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    data_root = tmp_path / "state"
    repo_root.mkdir()

    rendered = render_system_service(
        repo_root=repo_root,
        data_root=data_root,
        service_user="shiyi",
        service_group="shiyi",
    )

    assert "User=shiyi" in rendered
    assert "Group=shiyi" in rendered
    assert "WantedBy=multi-user.target" in rendered
    assert f"Environment=YIMIN_DATA_ROOT={data_root}" in rendered
    assert (
        f"ExecStart={repo_root / '.venv' / 'bin' / 'python'} -m agent.main --config "
        f"{repo_root / 'config' / 'agent.linux.yaml'}"
    ) in rendered


def test_system_scope_commands_do_not_use_user_flag() -> None:
    assert build_systemctl_command("restart", scope="system") == ["systemctl", "restart", "yimin"]
    assert build_systemctl_command("status", scope="system") == ["systemctl", "status", "yimin", "--no-pager"]
    assert build_journalctl_command(follow=True, scope="system") == ["journalctl", "-u", "yimin", "-f"]


def test_install_linux_script_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available in test environment")

    probe = subprocess.run(
        [bash, "-lc", "true"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("bash executable exists but is not runnable in this environment")

    result = subprocess.run(
        [bash, "-n", "scripts/install_linux.sh"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
