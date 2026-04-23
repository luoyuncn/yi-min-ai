"""CLI 入口测试。"""

import agent.cli.main as cli_main


class _ErroringApp:
    def handle_text(self, text: str, session_id: str) -> str:
        raise RuntimeError("provider boom")


def test_main_prints_runtime_error_without_traceback(monkeypatch, capsys) -> None:
    """运行期异常应转成可读错误，而不是让 CLI 直接崩溃。"""

    inputs = iter(["hi", "exit"])

    monkeypatch.setattr(cli_main, "build_app", lambda *args, **kwargs: _ErroringApp())
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.argv", ["agent.cli.main", "--config", "config/agent.yaml"])

    cli_main.main()

    captured = capsys.readouterr()
    assert "Atlas CLI is ready" in captured.out
    assert "Error: provider boom" in captured.out
