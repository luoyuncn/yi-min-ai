"""统一启动入口回归测试。"""

from click.testing import CliRunner

import agent.main as main_module


def test_main_handles_keyboard_interrupt_without_click_abort(monkeypatch) -> None:
    """Ctrl+C 应由应用自己优雅处理，而不是让 Click 打印 Aborted。"""

    def interrupt(*_args, **_kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "_run_cli", interrupt)

    result = CliRunner().invoke(main_module.main, ["--mode", "cli", "--testing"])

    assert result.exit_code == 0
    assert "收到停止信号" in result.output
    assert "Aborted!" not in result.output
