"""统一启动入口回归测试。"""

from click.testing import CliRunner

from agent.app import _build_system_prompt
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


def test_system_prompt_distinguishes_assistant_name_from_user_nickname() -> None:
    prompt = _build_system_prompt("Yi Min")

    assert "If the user says \"你叫 X\"" in prompt
    assert "Obsidian/Notion-like knowledge base" in prompt


def test_system_prompt_routes_fresh_facts_to_web_search() -> None:
    prompt = _build_system_prompt("Yi Min")

    assert "web_search" in prompt
    assert "current news" in prompt
    assert "Do not invent" in prompt
