"""Anthropic Provider 测试。"""

from types import SimpleNamespace

import pytest

import agent.providers.anthropic as anthropic_module
from agent.core.provider import LLMRequest, ProviderConfig
from agent.providers.anthropic import AnthropicProvider


@pytest.mark.asyncio
async def test_call_passes_common_generation_parameters_to_anthropic(monkeypatch) -> None:
    """Anthropic Provider 应透传公共生成参数，并允许请求级覆盖。"""

    captured: dict[str, object] = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic_module, "AsyncAnthropic", FakeAsyncAnthropic)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    provider = AnthropicProvider(
        ProviderConfig(
            name="claude-sonnet",
            provider_type="anthropic",
            model="claude-sonnet-4-20250514",
            api_key_env="ANTHROPIC_API_KEY",
            temperature=0.2,
            top_p=0.7,
            max_output_tokens=2048,
        )
    )
    await provider.initialize()

    response = await provider.call(
        LLMRequest(
            messages=[{"role": "user", "content": "Summarize this"}],
            top_p=0.9,
        )
    )

    assert captured["temperature"] == 0.2
    assert captured["top_p"] == 0.9
    assert captured["max_tokens"] == 2048
    assert response.text == "ok"
