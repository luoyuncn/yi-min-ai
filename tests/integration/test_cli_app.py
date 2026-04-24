"""集成测试：验证 build_app 与 CLI 真正能跑起来。"""

from pathlib import Path
import subprocess
import sys
import sqlite3

from agent.app import build_app


def test_build_app_wires_a_runnable_cli_agent(tmp_path: Path) -> None:
    """最小 testing app 应该能完成一次普通文本交互。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    config_dir.mkdir()
    skills.mkdir(parents=True)

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: claude-sonnet\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: claude-sonnet\n"
        "    type: anthropic\n"
        "    model: claude-sonnet-4-20250514\n"
        "    api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (skills / "daily-briefing").mkdir()
    (skills / "daily-briefing" / "SKILL.md").write_text(
        "---\nname: daily-briefing\ndescription: Generate daily briefing\n---\n# Daily Briefing\n",
        encoding="utf-8",
    )

    app = build_app(config_path=config_dir / "agent.yaml", testing=True)

    reply = app.handle_text("你好", session_id="cli:default")

    assert isinstance(reply, str)
    assert reply


def test_build_app_testing_mode_can_trigger_file_tool(tmp_path: Path) -> None:
    """testing 模式下也应能稳定走一遍工具调用链。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    config_dir.mkdir()
    skills.mkdir(parents=True)

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: claude-sonnet\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: claude-sonnet\n"
        "    type: anthropic\n"
        "    model: claude-sonnet-4-20250514\n"
        "    api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    app = build_app(config_path=config_dir / "agent.yaml", testing=True)

    reply = app.handle_text("读取 notes.txt", session_id="cli:default")

    assert reply == "已处理工具结果"
    db = sqlite3.connect(workspace / "agent.db")
    row_count = db.execute("select count(*) from sessions").fetchone()[0]
    assert row_count > 0


def test_cli_module_runs_and_prints_ready_banner(tmp_path: Path) -> None:
    """`python -m agent.cli.main` 必须真的执行 CLI 入口。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    config_dir.mkdir()
    skills.mkdir(parents=True)

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: claude-sonnet\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: claude-sonnet\n"
        "    type: anthropic\n"
        "    model: claude-sonnet-4-20250514\n"
        "    api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.cli.main",
            "--config",
            str(config_dir / "agent.yaml"),
            "--testing",
        ],
        input="exit\n",
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Yi Min CLI is ready" in result.stdout

