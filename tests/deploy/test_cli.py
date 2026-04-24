from argparse import Namespace
import subprocess

import pytest

import agent.deploy.cli as cli_module


def test_cmd_service_action_returns_systemctl_status_without_python_traceback(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, check):
        captured["command"] = command
        captured["check"] = check
        raise subprocess.CalledProcessError(returncode=3, cmd=command)

    monkeypatch.setattr(cli_module.sys, "platform", "linux")
    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(["status", "--scope", "system"])

    assert excinfo.value.code == 3
    assert captured["command"] == ["systemctl", "status", "yimin", "--no-pager"]
    assert captured["check"] is True
