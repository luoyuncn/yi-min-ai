import shutil
import subprocess
from pathlib import Path

import pytest

from agent.deploy.linux import build_journalctl_command, build_systemctl_command, render_system_service, render_user_service


def test_render_user_service_keeps_runtime_inside_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    rendered = render_user_service(
        repo_root=repo_root,
        config_path=repo_root / "config" / "agent.linux.yaml",
    )

    assert f"WorkingDirectory={repo_root}" in rendered
    assert "Environment=YIMIN_DATA_ROOT=" not in rendered
    assert (
        f"ExecStart={repo_root / 'scripts' / 'run_linux_service.sh'} "
        f"{repo_root / 'config' / 'agent.linux.yaml'}"
    ) in rendered


def test_systemd_commands_use_user_scope_and_service_name() -> None:
    assert build_systemctl_command("restart") == ["systemctl", "--user", "restart", "yimin"]
    assert build_systemctl_command("status") == ["systemctl", "--user", "status", "yimin", "--no-pager"]
    assert build_journalctl_command(follow=True) == ["journalctl", "--user", "-u", "yimin", "-f"]


def test_render_system_service_runs_as_target_user_and_targets_multi_user(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    rendered = render_system_service(
        repo_root=repo_root,
        service_user="shiyi",
        service_group="shiyi",
    )

    assert "User=shiyi" in rendered
    assert "Group=shiyi" in rendered
    assert "WantedBy=multi-user.target" in rendered
    assert "Environment=YIMIN_DATA_ROOT=" not in rendered
    assert (
        f"ExecStart={repo_root / 'scripts' / 'run_linux_service.sh'} "
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


def test_install_linux_script_repairs_uv_cache_permissions_for_sudo_user() -> None:
    script_text = Path("scripts/install_linux.sh").read_text(encoding="utf-8")

    assert 'mkdir -p "$user_home/.cache/uv"' in script_text
    assert 'chown -R "$SUDO_USER:$user_group" "$user_home/.cache/uv"' in script_text


def test_run_linux_service_script_prefers_repo_venv_python() -> None:
    script_text = Path("scripts/run_linux_service.sh").read_text(encoding="utf-8")

    assert '$REPO_ROOT/.venv/bin/python' in script_text
    assert 'exec "$REPO_ROOT/.venv/bin/python" -m agent.main --config "$CONFIG_PATH"' in script_text
    assert 'exec uv run python -m agent.main --config "$CONFIG_PATH"' in script_text


def test_linux_agent_config_keeps_all_workspaces_inside_repo() -> None:
    config_text = Path("config/agent.linux.yaml").read_text(encoding="utf-8")

    assert 'workspace_dir: "../workspace"' in config_text
    assert 'workspace_dir: "../workspace-main"' not in config_text
    assert 'workspace_dir: "../workspace-ops"' not in config_text
    assert "YIMIN_DATA_ROOT" not in config_text


def test_default_feishu_env_example_uses_single_bot_credentials() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "FEISHU_APP_ID=" in env_example
    assert "FEISHU_APP_SECRET=" in env_example
    assert "FEISHU_MIN_" not in env_example
    assert "FEISHU_MAIN_" not in env_example
    assert "FEISHU_OPS_" not in env_example
    assert "FEISHU_SALES_" not in env_example
