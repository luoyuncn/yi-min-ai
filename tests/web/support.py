"""Web 测试辅助函数。"""

from pathlib import Path


def write_testing_config(tmp_path: Path) -> Path:
    """写入一套最小可运行的 testing 配置。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    config_dir.mkdir()
    skills.mkdir(parents=True)

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Atlas\n"
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
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    return config_dir / "agent.yaml"
