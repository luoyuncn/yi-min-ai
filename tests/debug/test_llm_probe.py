"""LLM 诊断脚本测试。"""

from pathlib import Path

import pytest

from agent.config.models import AgentSettings, ProviderConfigItem, ProviderSettings, Settings
from agent.core.provider import LLMRequest, LLMResponse, LLMStreamChunk
from agent.debug.llm_probe import build_probe_config, build_probe_request, probe_manager


def _build_settings() -> Settings:
    return Settings(
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


def test_build_probe_config_disables_thinking_for_qwen() -> None:
    """诊断脚本应支持显式关闭 Qwen 的 thinking。"""

    config = build_probe_config(_build_settings(), provider_name="qwen", thinking="off")

    assert config.extra_body == {"enable_thinking": False}


def test_build_probe_config_maps_thinking_override_for_deepseek() -> None:
    """诊断脚本应把通用 thinking 开关映射为 DeepSeek 官方参数结构。"""

    settings = Settings(
        agent=AgentSettings(
            name="Atlas",
            workspace_dir=Path("/tmp/workspace"),
            max_iterations=8,
        ),
        providers=ProviderSettings(
            config_file=Path("/tmp/providers.yaml"),
            default_primary="deepseek",
            items=[
                ProviderConfigItem(
                    name="deepseek",
                    provider_type="openai",
                    model="deepseek-v4-pro",
                    api_key_env="DEEPSEEK_API_KEY",
                ),
            ],
        ),
    )

    disabled = build_probe_config(settings, provider_name="deepseek", thinking="off")
    enabled = build_probe_config(settings, provider_name="deepseek", thinking="on")

    assert disabled.extra_body == {"thinking": {"type": "disabled"}}
    assert enabled.extra_body == {"thinking": {"type": "enabled"}}


def test_build_probe_request_supports_request_level_overrides() -> None:
    """诊断脚本应支持单次请求覆盖公共生成参数。"""

    request = build_probe_request(
        "请只回复 OK",
        temperature=0.2,
        top_p=0.7,
        max_tokens=256,
    )

    assert request.messages == [{"role": "user", "content": "请只回复 OK"}]
    assert request.temperature == 0.2
    assert request.top_p == 0.7
    assert request.max_tokens == 256


@pytest.mark.asyncio
async def test_probe_manager_collects_latency_and_response_text() -> None:
    """诊断脚本应统计首 token 与总耗时，并返回最终文本。"""

    class FakeManager:
        async def call_stream(self, request):
            yield LLMStreamChunk(type="text_delta", delta="你")
            yield LLMStreamChunk(type="text_delta", delta="好")
            yield LLMStreamChunk(
                type="response",
                response=LLMResponse(
                    type="text",
                    text="你好",
                    usage={"input_tokens": 3, "output_tokens": 2},
                ),
            )

    timestamps = iter([10.0, 10.4, 11.2])

    result = await probe_manager(
        FakeManager(),
        LLMRequest(messages=[{"role": "user", "content": "你好"}]),
        clock=lambda: next(timestamps),
    )

    assert result.first_token_seconds == pytest.approx(0.4)
    assert result.total_seconds == pytest.approx(1.2)
    assert result.text == "你好"
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}
