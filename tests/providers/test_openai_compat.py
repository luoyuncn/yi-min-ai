"""OpenAI 兼容 Provider 测试。"""

import pytest

import agent.providers.openai_compat as openai_compat_module
from agent.core.provider import ProviderConfig
from agent.providers.openai_compat import OpenAICompatProvider


def test_initialize_appends_v1_for_bare_base_url(monkeypatch) -> None:
    """自定义兼容端点如果只有 host，应自动补上 `/v1`。"""

    captured: dict[str, str] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="gpt-5",
            provider_type="openai",
            model="gpt-5",
            api_key_env="OPENAI_API_KEY",
            base_url="http://example.com:8888",
        )
    )

    import asyncio

    asyncio.run(provider.initialize())

    assert captured["base_url"] == "http://example.com:8888/v1"


def test_convert_response_raises_helpful_error_for_html_page() -> None:
    """如果兼容端点返回 HTML 页面，应给出可读错误信息。"""

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="gpt-5",
            provider_type="openai",
            model="gpt-5",
            api_key_env="OPENAI_API_KEY",
            base_url="http://example.com:8888",
        )
    )

    with pytest.raises(ValueError, match="returned HTML"):
        provider._convert_response("<!doctype html><html><body>Gateway</body></html>")
