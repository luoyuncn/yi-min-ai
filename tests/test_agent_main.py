"""统一启动入口回归测试。"""

from click.testing import CliRunner

from agent.app import _build_system_prompt, _ensure_workspace_files
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

    assert "如果用户说“你叫 X”" in prompt
    assert "Obsidian/Notion 式知识库" in prompt


def test_system_prompt_routes_fresh_facts_to_web_search() -> None:
    prompt = _build_system_prompt("Yi Min")

    assert "web_search" in prompt
    assert "当前新闻" in prompt
    assert "不要编造实时事实" in prompt


def test_workspace_init_uses_default_soul_template(tmp_path) -> None:
    _ensure_workspace_files(tmp_path)

    soul_text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")

    assert "你是银月，本名玲珑" in soul_text
    assert "银月狼族圣女" in soul_text
    assert "不在没有把握时假装什么都知道" in soul_text


def test_workspace_init_uses_profile_file_instead_of_memory_file(tmp_path) -> None:
    _ensure_workspace_files(tmp_path)

    assert (tmp_path / "PROFILE.md").read_text(encoding="utf-8") == "# User Profile\n"
    assert not (tmp_path / "MEMORY.md").exists()


def test_workspace_init_migrates_legacy_memory_to_profile(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("# User Profile\n- legacy fact\n", encoding="utf-8")

    _ensure_workspace_files(tmp_path)

    assert "legacy fact" in (tmp_path / "PROFILE.md").read_text(encoding="utf-8")
