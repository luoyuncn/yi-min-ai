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

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.agent.name == "Yi Min"
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
        "  name: Yi Min\n"
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
        "  name: Yi Min\n"
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
        "  name: Yi Min\n"
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
        "  name: Yi Min\n"
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
        "  name: Yi Min\n"
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
        "  name: Yi Min\n"
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


def test_load_settings_uses_first_channel_workspace_as_base_workspace(tmp_path: Path) -> None:
    """配置了 channels.instances 时，不应再保留独立默认 workspace。"""

    config_dir = tmp_path / "config"
    default_workspace = tmp_path / "workspace"
    channel_a_workspace = tmp_path / "workspace-main"
    channel_b_workspace = tmp_path / "workspace-ops"
    config_dir.mkdir()
    default_workspace.mkdir()
    channel_a_workspace.mkdir()
    channel_b_workspace.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n"
        "channels:\n"
        "  instances:\n"
        "    - name: feishu-main\n"
        "      type: feishu\n"
        "      workspace_dir: ../workspace-main\n"
        "      app_id_env: FEISHU_MAIN_APP_ID\n"
        "      app_secret_env: FEISHU_MAIN_APP_SECRET\n"
        "    - name: feishu-ops\n"
        "      type: feishu\n"
        "      workspace_dir: ../workspace-ops\n"
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

    assert settings.agent.workspace_dir == channel_a_workspace.resolve()


def test_load_settings_allows_omitting_agent_workspace_when_channels_exist(tmp_path: Path) -> None:
    """只要声明了 channels.instances，就不该强制要求独立默认 workspace。"""

    config_dir = tmp_path / "config"
    channel_a_workspace = tmp_path / "workspace-main"
    config_dir.mkdir()
    channel_a_workspace.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n"
        "channels:\n"
        "  instances:\n"
        "    - name: feishu-main\n"
        "      type: feishu\n"
        "      workspace_dir: ../workspace-main\n"
        "      app_id_env: FEISHU_MAIN_APP_ID\n"
        "      app_secret_env: FEISHU_MAIN_APP_SECRET\n",
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

    assert settings.agent.workspace_dir == channel_a_workspace.resolve()


def test_load_settings_parses_mflow_embedding_configuration(tmp_path: Path) -> None:
    """M-flow 配置应能独立解析，并支持 embedding provider 引用。"""

    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    mflow_dir = tmp_path / "mflow-store"
    config_dir.mkdir()
    workspace_dir.mkdir()
    mflow_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: deepseek\n"
        "mflow:\n"
        "  enabled: true\n"
        "  data_dir: ../mflow-store\n"
        "  dataset_name: workspace-memory\n"
        "  llm_provider_name: deepseek\n"
        "  embedding:\n"
        "    provider_name: qwen\n"
        "    model: text-embedding-v4\n"
        "    dimensions: 1024\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    type: openai\n"
        "    model: deepseek-v4-flash\n"
        "    api_key_env: DEEPSEEK_API_KEY\n"
        "    base_url: https://api.deepseek.com/v1\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen3.6-plus\n"
        "    api_key_env: DASHSCOPE_API_KEY\n"
        "    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.mflow is not None
    assert settings.mflow.enabled is True
    assert settings.mflow.data_dir == mflow_dir.resolve()
    assert settings.mflow.dataset_name == "workspace-memory"
    assert settings.mflow.llm_provider_name == "deepseek"
    assert settings.mflow.embedding is not None
    assert settings.mflow.embedding.provider_name == "qwen"
    assert settings.mflow.embedding.model == "text-embedding-v4"
    assert settings.mflow.embedding.dimensions == 1024


def test_load_settings_expands_environment_variables_in_path_fields(tmp_path: Path, monkeypatch) -> None:
    """路径字段应支持 `${VAR:-fallback}` 形式的环境变量展开。"""

    config_dir = tmp_path / "config"
    state_root = tmp_path / "state-root"
    config_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ${YIMIN_DATA_ROOT:-../fallback}/default\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: deepseek\n"
        "mflow:\n"
        "  enabled: true\n"
        "  data_dir: ${YIMIN_DATA_ROOT:-../fallback}/mflow\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    type: openai\n"
        "    model: deepseek-v4-flash\n"
        "    api_key_env: DEEPSEEK_API_KEY\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("YIMIN_DATA_ROOT", str(state_root))

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.agent.workspace_dir == (state_root / "default").resolve()
    assert settings.mflow is not None
    assert settings.mflow.data_dir == (state_root / "mflow").resolve()

