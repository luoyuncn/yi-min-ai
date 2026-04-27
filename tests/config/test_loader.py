"""配置加载器测试。

这些测试主要保护两类行为：
1. 正常配置能被正确解析
2. 配置损坏时，调用方能拿到稳定可预期的 ConfigError
"""

from pathlib import Path

import pytest

from agent.config.loader import ConfigError, is_multi_runtime_settings, load_settings


def test_default_agent_config_uses_single_workspace_subject() -> None:
    """本地默认配置应该只描述一个 Yi Min 主体和一个 workspace。"""

    settings = load_settings(Path("config/agent.yaml"))

    assert settings.agent.workspace_dir == (Path("workspace").resolve())
    assert settings.channels is None or len(settings.channels.instances) <= 1
    if settings.channels is not None and settings.channels.instances:
        instance = settings.channels.instances[0]
        assert instance.name == "feishu"
        assert instance.workspace_dir == Path("workspace").resolve()


def test_default_linux_config_uses_one_feishu_channel() -> None:
    """Linux 生产配置不应默认声明多个飞书机器人。"""

    settings = load_settings(Path("config/agent.linux.yaml"))

    assert settings.agent.workspace_dir == Path("workspace").resolve()
    assert settings.channels is not None
    assert len(settings.channels.instances) == 1
    instance = settings.channels.instances[0]
    assert instance.name == "feishu"
    assert instance.channel_type == "feishu"
    assert instance.workspace_dir == Path("workspace").resolve()
    assert instance.app_id_env == "FEISHU_APP_ID"
    assert instance.app_secret_env == "FEISHU_APP_SECRET"


def test_single_channel_instance_is_not_multi_runtime() -> None:
    """配置一个飞书实例只是单主体生产形态，不应禁用主动调度。"""

    settings = load_settings(Path("config/agent.linux.yaml"))

    assert is_multi_runtime_settings(settings) is False


def test_default_configs_disable_mflow_by_default() -> None:
    """M-flow 太重，默认配置不应把它接进主链路。"""

    local_settings = load_settings(Path("config/agent.yaml"))
    linux_settings = load_settings(Path("config/agent.linux.yaml"))

    assert local_settings.mflow is not None
    assert local_settings.mflow.enabled is False
    assert linux_settings.mflow is not None
    assert linux_settings.mflow.enabled is False


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


def test_load_settings_parses_shell_tool_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n"
        "tools:\n"
        "  shell:\n"
        "    enabled: true\n"
        "    requires_confirmation: true\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen3.6-plus\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.tools.shell.enabled is True
    assert settings.tools.shell.requires_confirmation is True


def test_load_settings_parses_langfuse_observability_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n"
        "observability:\n"
        "  langfuse:\n"
        "    enabled: true\n"
        "    public_key_env: LANGFUSE_PUBLIC_KEY\n"
        "    secret_key_env: LANGFUSE_SECRET_KEY\n"
        "    base_url: http://192.169.26.221:3000\n"
        "    capture_inputs: true\n"
        "    capture_outputs: true\n"
        "    capture_tool_args: true\n"
        "    capture_tool_results: true\n"
        "    capture_reasoning: metadata\n"
        "    max_field_chars: 12000\n"
        "    timeout_seconds: 15\n"
        "    flush_interval_seconds: 2\n"
        "    flush_at: 32\n"
        "    flush_on_run_end: false\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen3.6-plus\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.observability.langfuse.enabled is True
    assert settings.observability.langfuse.base_url == "http://192.169.26.221:3000"
    assert settings.observability.langfuse.capture_reasoning == "metadata"
    assert settings.observability.langfuse.timeout_seconds == 15
    assert settings.observability.langfuse.flush_interval_seconds == 2
    assert settings.observability.langfuse.flush_at == 32
    assert settings.observability.langfuse.flush_on_run_end is False


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

