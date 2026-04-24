"""配置加载器测试。

这些测试主要保护两类行为：
1. 正常配置能被正确解析
2. 配置损坏时，调用方能拿到稳定可预期的 ConfigError
"""

from pathlib import Path

import pytest

from agent.config.loader import ConfigError, load_settings


def test_load_settings_resolves_workspace_and_default_provider(tmp_path: Path) -> None:
    """验证 happy path：主配置和 provider 配置能被拼成 Settings。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

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

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.agent.name == "Atlas"
    assert settings.agent.workspace_dir == workspace_dir.resolve()
    assert settings.providers.default_primary == "claude-sonnet"
    assert settings.providers.items[0].model == "claude-sonnet-4-20250514"


def test_load_settings_rejects_missing_required_agent_name(tmp_path: Path) -> None:
    """缺少关键字段时，应该返回带上下文的 ConfigError。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
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

    with pytest.raises(ConfigError, match="agent.name"):
        load_settings(config_dir / "agent.yaml")


def test_load_settings_rejects_unknown_default_primary(tmp_path: Path) -> None:
    """`default_primary` 必须指向已声明的 provider。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Atlas\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: missing-provider\n",
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

    with pytest.raises(ConfigError, match="default_primary"):
        load_settings(config_dir / "agent.yaml")


def test_load_settings_wraps_missing_provider_file_in_config_error(tmp_path: Path) -> None:
    """底层文件缺失也应该被包装成统一配置异常。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

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

    with pytest.raises(ConfigError, match="Unable to read"):
        load_settings(config_dir / "agent.yaml")


def test_load_settings_wraps_invalid_yaml_in_config_error(tmp_path: Path) -> None:
    """YAML 解析错误也应该收敛到 ConfigError。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

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
    (config_dir / "providers.yaml").write_text("providers: [", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_settings(config_dir / "agent.yaml")


def test_load_settings_parses_provider_extra_body(tmp_path: Path) -> None:
    """Provider 可选的 extra_body 应能从 YAML 正确解析出来。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Atlas\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen3.6-plus\n"
        "    api_key_env: OPENAI_API_KEY\n"
        "    extra_body:\n"
        "      enable_thinking: false\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.providers.items[0].extra_body == {"enable_thinking": False}


def test_load_settings_parses_optional_generation_parameters(tmp_path: Path) -> None:
    """Provider 可选的公共生成参数应能从 YAML 正确解析出来。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Atlas\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: gpt-5\n"
        "    type: openai\n"
        "    model: gpt-5.4\n"
        "    api_key_env: OPENAI_API_KEY\n"
        "    temperature: 0.4\n"
        "    top_p: 0.9\n"
        "    max_output_tokens: 4096\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")
    provider = settings.providers.items[0]

    assert provider.temperature == 0.4
    assert provider.top_p == 0.9
    assert provider.max_output_tokens == 4096


def test_load_settings_parses_channel_instances_with_independent_workspaces(tmp_path: Path) -> None:
    """多渠道实例配置应能解析为独立 workspace 的运行时定义。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    channel_a_workspace = tmp_path / "workspace-a"
    channel_b_workspace = tmp_path / "workspace-b"
    config_dir.mkdir()
    workspace_dir.mkdir()
    channel_a_workspace.mkdir()
    channel_b_workspace.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Atlas\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n"
        "channels:\n"
        "  instances:\n"
        "    - name: feishu-main\n"
        "      type: feishu\n"
        "      workspace_dir: ../workspace-a\n"
        "      app_id_env: FEISHU_MAIN_APP_ID\n"
        "      app_secret_env: FEISHU_MAIN_APP_SECRET\n"
        "    - name: feishu-ops\n"
        "      type: feishu\n"
        "      workspace_dir: ../workspace-b\n"
        "      app_id_env: FEISHU_OPS_APP_ID\n"
        "      app_secret_env: FEISHU_OPS_APP_SECRET\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: gpt-5\n"
        "    type: openai\n"
        "    model: gpt-5.4\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.channels is not None
    assert [item.name for item in settings.channels.instances] == ["feishu-main", "feishu-ops"]
    assert settings.channels.instances[0].workspace_dir == channel_a_workspace.resolve()
    assert settings.channels.instances[1].workspace_dir == channel_b_workspace.resolve()
