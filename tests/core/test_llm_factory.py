"""LLMFactory 测试。"""

from pathlib import Path

from agent.config.models import AgentSettings, ProviderConfigItem, ProviderSettings, Settings
from agent.core.llm_factory import LLMFactory


def test_llm_factory_enables_thinking_by_default_for_qwen36_models() -> None:
    """Qwen3.6 模型默认应显式开启 thinking，避免行为依赖外部端点默认值。"""

    config = LLMFactory.create(
        ProviderConfigItem(
            name="qwen",
            provider_type="openai",
            model="qwen3.6-plus",
            api_key_env="OPENAI_API_KEY",
        )
    )

    assert config.extra_body == {"enable_thinking": True}


def test_llm_factory_prefers_config_and_runtime_over_model_defaults() -> None:
    """模型默认值应允许被静态配置和运行时参数逐层覆盖。"""

    config = LLMFactory.create(
        ProviderConfigItem(
            name="qwen",
            provider_type="openai",
            model="qwen3.6-plus",
            api_key_env="OPENAI_API_KEY",
            extra_body={"search_strategy": "hybrid", "enable_thinking": True},
        ),
        extra_body={"response_format": "json"},
        enable_thinking=False,
        max_output_tokens=4096,
    )

    assert config.extra_body == {
        "enable_thinking": False,
        "search_strategy": "hybrid",
        "response_format": "json",
    }
    assert config.max_output_tokens == 4096


def test_llm_factory_can_build_primary_provider_from_settings() -> None:
    """应用装配层应能通过工厂直接拿到 primary provider 的运行时配置。"""

    settings = Settings(
        agent=AgentSettings(
            name="Atlas",
            workspace_dir=Path("/tmp/workspace"),
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=Path("/tmp/providers.yaml"),
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

    config = LLMFactory.create_primary(settings)

    assert config.name == "qwen"
    assert config.model == "qwen3.6-plus"
    assert config.extra_body == {"enable_thinking": True}


def test_llm_factory_supports_optional_common_generation_parameters() -> None:
    """工厂应支持温度、top_p 和输出上限等可选公共参数。"""

    config = LLMFactory.create(
        ProviderConfigItem(
            name="gpt-5",
            provider_type="openai",
            model="gpt-5.4",
            api_key_env="OPENAI_API_KEY",
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=3000,
        ),
        temperature=0.6,
    )

    assert config.temperature == 0.6
    assert config.top_p == 0.8
    assert config.max_output_tokens == 3000
