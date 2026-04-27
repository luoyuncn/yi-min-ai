"""应用装配层测试。"""

import asyncio
import logging
import os
from pathlib import Path

import agent.core.provider_manager as provider_manager_module
import agent.app as app_module
from litellm.litellm_core_utils.get_llm_provider_logic import get_llm_provider
from agent.app import _build_provider_manager, _build_system_prompt, build_app, build_channel_apps_async
from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    MflowEmbeddingSettings,
    MflowSettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
)
from agent.core.provider import LLMRequest, LLMResponse, ProviderConfig


class MissingKeyAnthropicProvider:
    """模拟缺少密钥的非主 Provider。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def initialize(self) -> None:
        raise ValueError(f"Missing API key: {self.config.api_key_env}")

    async def call(self, request: LLMRequest) -> LLMResponse:
        raise AssertionError("Non-primary provider should not be called")


class FakeOpenAIProvider:
    """模拟可正常初始化的主 Provider。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def initialize(self) -> None:
        return None

    async def call(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            type="text",
            text="pong",
            provider=self.config.name,
            model=self.config.model,
        )


def test_build_provider_manager_only_requires_primary_provider(monkeypatch, tmp_path: Path) -> None:
    """真实启动时不应因为未使用的 Provider 缺少密钥而失败。"""

    monkeypatch.setattr(provider_manager_module, "AnthropicProvider", MissingKeyAnthropicProvider)
    monkeypatch.setattr(provider_manager_module, "OpenAICompatProvider", FakeOpenAIProvider)

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="gpt-5",
            items=[
                ProviderConfigItem(
                    name="claude-sonnet",
                    provider_type="anthropic",
                    model="claude-sonnet-4-20250514",
                    api_key_env="ANTHROPIC_API_KEY",
                ),
                ProviderConfigItem(
                    name="gpt-5",
                    provider_type="openai",
                    model="gpt-5",
                    api_key_env="OPENAI_API_KEY",
                ),
            ],
        ),
    )

    manager = _build_provider_manager(settings)
    response = asyncio.run(manager.call(LLMRequest(messages=[{"role": "user", "content": "ping"}])))

    assert response.text == "pong"
    assert response.provider == "gpt-5"


def test_build_app_loads_api_key_from_root_dotenv(monkeypatch, tmp_path: Path) -> None:
    """真实模式启动时应自动加载项目根目录下的 .env。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    workspace.mkdir()

    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-test-key\n", encoding="utf-8")
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
        "    model: gpt-5\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    captured: dict[str, str | None] = {}

    class CapturingOpenAIProvider:
        def __init__(self, config: ProviderConfig) -> None:
            self.config = config

        async def initialize(self) -> None:
            captured["api_key"] = os.environ.get(self.config.api_key_env)

        async def call(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(type="text", text="pong", provider=self.config.name, model=self.config.model)

    monkeypatch.setattr(provider_manager_module, "OpenAICompatProvider", CapturingOpenAIProvider)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        build_app(config_path=config_dir / "agent.yaml", testing=False)
    finally:
        os.chdir(original_cwd)

    assert captured["api_key"] == "dotenv-test-key"


def test_build_provider_manager_passes_extra_body_to_primary_provider(monkeypatch, tmp_path: Path) -> None:
    """primary provider 的 extra_body 应被装配到运行时 ProviderConfig。"""

    captured: dict[str, object] = {}

    class CapturingOpenAIProvider:
        def __init__(self, config: ProviderConfig) -> None:
            captured["extra_body"] = config.extra_body
            self.config = config

        async def initialize(self) -> None:
            return None

        async def call(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(type="text", text="pong", provider=self.config.name, model=self.config.model)

    monkeypatch.setattr(provider_manager_module, "OpenAICompatProvider", CapturingOpenAIProvider)

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen3.6-plus",
                    api_key_env="OPENAI_API_KEY",
                    extra_body={"enable_thinking": False},
                ),
            ],
        ),
    )

    _build_provider_manager(settings)

    assert captured["extra_body"] == {"enable_thinking": False}


def test_build_provider_manager_applies_llm_factory_defaults_for_primary_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """primary provider 未显式配置 extra_body 时，也应走工厂的模型默认值。"""

    captured: dict[str, object] = {}

    class CapturingOpenAIProvider:
        def __init__(self, config: ProviderConfig) -> None:
            captured["extra_body"] = config.extra_body
            self.config = config

        async def initialize(self) -> None:
            return None

        async def call(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(type="text", text="pong", provider=self.config.name, model=self.config.model)

    monkeypatch.setattr(provider_manager_module, "OpenAICompatProvider", CapturingOpenAIProvider)

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen3.6-plus",
                    api_key_env="OPENAI_API_KEY",
                ),
            ],
        ),
    )

    _build_provider_manager(settings)

    assert captured["extra_body"] == {"enable_thinking": True}


def test_build_channel_apps_async_uses_runtime_workspace_overrides(tmp_path: Path) -> None:
    """多渠道实例模式下，应为每个 runtime 构建独立 workspace 的 app。"""

    config_dir = tmp_path / "config"
    default_workspace = tmp_path / "workspace"
    main_workspace = tmp_path / "workspace-main"
    ops_workspace = tmp_path / "workspace-ops"
    config_dir.mkdir()
    default_workspace.mkdir()
    main_workspace.mkdir()
    ops_workspace.mkdir()

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

    _, apps = asyncio.run(build_channel_apps_async(config_dir / "agent.yaml", testing=True))

    assert set(apps.keys()) == {"feishu-main", "feishu-ops"}
    assert apps["feishu-main"].core.workspace_dir == main_workspace.resolve()
    assert apps["feishu-ops"].core.workspace_dir == ops_workspace.resolve()


def test_build_channel_apps_async_defaults_mflow_data_dir_per_workspace(tmp_path: Path, monkeypatch) -> None:
    """未显式配置 mflow.data_dir 时，每个 workspace 都应拥有自己的独立目录。"""

    config_dir = tmp_path / "config"
    default_workspace = tmp_path / "workspace"
    main_workspace = tmp_path / "workspace-main"
    ops_workspace = tmp_path / "workspace-ops"
    config_dir.mkdir()
    default_workspace.mkdir()
    main_workspace.mkdir()
    ops_workspace.mkdir()

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
        "  embedding:\n"
        "    provider_name: qwen\n"
        "    model: text-embedding-v4\n"
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

    captured_data_dirs: list[Path] = []

    class DummyMflowBridge:
        def __init__(self, *, data_dir, runtime_config):
            captured_data_dirs.append(Path(data_dir))
            self.is_available = False

        async def initialize(self) -> None:
            return None

    monkeypatch.setattr(app_module, "MflowBridge", DummyMflowBridge)

    asyncio.run(build_channel_apps_async(config_dir / "agent.yaml", testing=True))

    assert captured_data_dirs == [
        main_workspace.resolve() / "mflow_data",
        ops_workspace.resolve() / "mflow_data",
    ]


def test_build_app_does_not_initialize_mflow_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """默认禁用 M-flow 时，应用装配不应实例化 MflowBridge。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    workspace.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: gpt-5\n"
        "mflow:\n"
        "  enabled: false\n",
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

    class FailingMflowBridge:
        def __init__(self, *, data_dir, runtime_config):
            raise AssertionError("MflowBridge should not be constructed when disabled")

    monkeypatch.setattr(app_module, "MflowBridge", FailingMflowBridge)

    app = build_app(config_path=config_dir / "agent.yaml", testing=True)

    assert app.core.mflow_bridge is None


def test_build_app_scaffolds_default_bookkeeping_and_note_taking_skills(tmp_path: Path) -> None:
    """新 workspace 应自动提供记账和自动笔记 skill 模板。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    workspace.mkdir()

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
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    build_app(config_path=config_dir / "agent.yaml", testing=True)

    bookkeeping_skill = workspace / "skills" / "bookkeeping" / "SKILL.md"
    note_taking_skill = workspace / "skills" / "note-taking" / "SKILL.md"

    assert bookkeeping_skill.exists()
    assert note_taking_skill.exists()
    bookkeeping_text = bookkeeping_skill.read_text(encoding="utf-8")
    note_taking_text = note_taking_skill.read_text(encoding="utf-8")

    assert "bookkeeping" in bookkeeping_text
    assert "ledger_upsert_draft" in bookkeeping_text
    assert "If the user expresses income, expense, reimbursement" in bookkeeping_text
    assert "note-taking" in note_taking_text
    assert "Always save when the user explicitly asks to remember something" in note_taking_text
    assert "Search existing notes before creating a new one" in note_taking_text
    assert "Do not auto-save one-off small talk" in note_taking_text


def test_build_app_scaffolds_scheduler_templates(tmp_path: Path) -> None:
    """新 workspace 应自动生成 Heartbeat 与 Cron 模板文件。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_dir.mkdir()
    workspace.mkdir()

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
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )

    build_app(config_path=config_dir / "agent.yaml", testing=True)

    heartbeat_file = workspace / "HEARTBEAT.md"
    cron_file = workspace / "CRON.yaml"

    assert heartbeat_file.exists()
    assert cron_file.exists()
    assert "HEARTBEAT_OK" in heartbeat_file.read_text(encoding="utf-8")
    assert "tasks:" in cron_file.read_text(encoding="utf-8")


def test_build_system_prompt_includes_bookkeeping_and_note_taking_policy() -> None:
    """系统提示词应主动引导模型使用账本与长期笔记工具。"""

    prompt = _build_system_prompt("Yi Min")

    assert "TOOL ROUTING POLICY" in prompt
    assert "Use ledger tools for bookkeeping requests" in prompt
    assert "Ask follow-up questions before committing incomplete ledger entries" in prompt
    assert "Use note tools for long-lived user facts" in prompt
    assert "Do not store bookkeeping or note facts in MEMORY.md" in prompt
    assert "When the user asks who they are, what their name is" in prompt
    assert "When asked about your available tools or skills" in prompt


def test_build_mflow_llm_config_qualifies_dashscope_model_for_litellm(tmp_path: Path) -> None:
    """DashScope 兼容端点上的自定义模型名应带 LiteLLM provider 前缀。"""

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen3.6-plus",
                    api_key_env="DASHSCOPE_API_KEY",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
            ],
        ),
        mflow=MflowSettings(enabled=True, llm_provider_name="qwen"),
    )

    llm_config = app_module._build_mflow_llm_config(settings, provider_name="qwen")
    resolved_model, resolved_provider, _, resolved_base = get_llm_provider(
        model=llm_config.model,
        api_base=llm_config.base_url,
    )

    assert llm_config.provider == "custom"
    assert llm_config.model == "dashscope/qwen3.6-plus"
    assert resolved_model == "qwen3.6-plus"
    assert resolved_provider == "dashscope"
    assert resolved_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_mflow_embedding_config_qualifies_dashscope_model_for_litellm(tmp_path: Path) -> None:
    """DashScope 兼容 embedding 应走 OpenAI-compatible 路由。"""

    settings = Settings(
        agent=AgentSettings(
            name="Yi Min",
            workspace_dir=tmp_path / "workspace",
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=tmp_path / "providers.yaml",
            default_primary="qwen",
            items=[
                ProviderConfigItem(
                    name="qwen",
                    provider_type="openai",
                    model="qwen3.6-plus",
                    api_key_env="DASHSCOPE_API_KEY",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
            ],
        ),
        mflow=MflowSettings(
            enabled=True,
            embedding=MflowEmbeddingSettings(
                provider_name="qwen",
                model="text-embedding-v4",
                dimensions=1024,
                batch_size=10,
            ),
        ),
    )

    embedding_config = app_module._build_mflow_embedding_config(settings, settings.mflow)
    assert embedding_config is not None

    resolved_model, resolved_provider, _, resolved_base = get_llm_provider(
        model=embedding_config.model,
        api_base=embedding_config.base_url,
    )

    assert embedding_config.provider == "openai"
    assert embedding_config.model == "openai/text-embedding-v4"
    assert embedding_config.dimensions == 1024
    assert embedding_config.batch_size == 10
    assert resolved_model == "text-embedding-v4"
    assert resolved_provider == "openai"
    assert resolved_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_app_resolves_mflow_runtime_config_from_provider_references(tmp_path: Path, monkeypatch) -> None:
    """build_app 应把主 LLM 与 embedding provider 正确映射到 M-flow。"""

    config_dir = tmp_path / "config"
    workspace = tmp_path / "workspace"
    mflow_dir = tmp_path / "shared-mflow"
    config_dir.mkdir()
    workspace.mkdir()
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
        "  data_dir: ../shared-mflow\n"
        "  embedding:\n"
        "    provider_name: qwen\n"
        "    model: text-embedding-v4\n",
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

    captured: dict[str, object] = {}

    class DummyMflowBridge:
        def __init__(self, *, data_dir, runtime_config):
            captured["data_dir"] = data_dir
            captured["runtime_config"] = runtime_config
            self.is_available = False

        async def initialize(self) -> None:
            captured["initialized"] = True

    monkeypatch.setattr(app_module, "MflowBridge", DummyMflowBridge)

    build_app(config_path=config_dir / "agent.yaml", testing=True)

    runtime_config = captured["runtime_config"]
    assert captured["initialized"] is True
    assert Path(captured["data_dir"]) == mflow_dir.resolve()
    assert runtime_config.dataset_name == "workspace"
    assert runtime_config.llm.provider == "custom"
    assert runtime_config.llm.model == "deepseek/deepseek-v4-flash"
    assert runtime_config.llm.base_url == "https://api.deepseek.com/v1"
    assert runtime_config.embedding.provider == "openai"
    assert runtime_config.embedding.model == "openai/text-embedding-v4"
    assert runtime_config.embedding.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_channel_apps_async_logs_runtime_startup_progress(tmp_path: Path, monkeypatch, caplog) -> None:
    """多 runtime 装配时应把每个阶段写进宿主日志，便于定位卡点。"""

    config_dir = tmp_path / "config"
    main_workspace = tmp_path / "workspace-main"
    ops_workspace = tmp_path / "workspace-ops"
    config_dir.mkdir()
    main_workspace.mkdir()
    ops_workspace.mkdir()

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

    class DummyMflowBridge:
        def __init__(self, *, data_dir, runtime_config):
            self.is_available = False

        async def initialize(self) -> None:
            return None

    monkeypatch.setattr(app_module, "MflowBridge", DummyMflowBridge)
    caplog.set_level(logging.INFO, logger="agent.app")

    asyncio.run(build_channel_apps_async(config_dir / "agent.yaml", testing=True))

    assert "event=runtime_build_started runtime=feishu-main" in caplog.text
    assert "event=runtime_build_started runtime=feishu-ops" in caplog.text
    assert "event=provider_manager_ready" in caplog.text
    assert "event=mflow_bridge_ready" in caplog.text
    assert "event=app_bootstrap_completed" in caplog.text

