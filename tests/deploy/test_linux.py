from pathlib import Path

from agent.deploy.linux import build_journalctl_command, build_systemctl_command, render_user_service


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
