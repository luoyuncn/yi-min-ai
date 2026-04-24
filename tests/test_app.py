"""应用装配层测试。"""

import asyncio
import os
from pathlib import Path

import agent.core.provider_manager as provider_manager_module
from agent.app import _build_provider_manager, build_app, build_channel_apps_async
from agent.config.models import AgentSettings, ChannelInstanceSettings, ChannelSettings, ProviderConfigItem, ProviderSettings, Settings
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
            name="Atlas",
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
            name="Atlas",
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
            name="Atlas",
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
