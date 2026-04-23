"""OpenAI 兼容 Provider 测试。"""

from types import SimpleNamespace

import pytest

import agent.providers.openai_compat as openai_compat_module
from agent.core.provider import LLMRequest, ProviderConfig
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


@pytest.mark.asyncio
async def test_call_stream_yields_text_deltas_and_final_response(monkeypatch) -> None:
    """OpenAI 兼容 Provider 应把 SDK stream 转成统一 chunk。"""

    captured: dict[str, object] = {}

    class FakeStream:
        def __init__(self, chunks) -> None:
            self._chunks = chunks

        def __aiter__(self):
            self._iter = iter(self._chunks)
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeStream(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="你", tool_calls=None)
                            )
                        ],
                        usage=None,
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="好", tool_calls=None)
                            )
                        ],
                        usage=None,
                    ),
                    SimpleNamespace(
                        choices=[],
                        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
                    ),
                ]
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="gpt-5",
            provider_type="openai",
            model="gpt-5",
            api_key_env="OPENAI_API_KEY",
        )
    )
    await provider.initialize()

    chunks = [
        chunk
        async for chunk in provider.call_stream(
            LLMRequest(messages=[{"role": "user", "content": "你好"}])
        )
    ]

    assert captured["stream"] is True
    assert [chunk.delta for chunk in chunks if chunk.type == "text_delta"] == ["你", "好"]
    final_chunk = chunks[-1]
    assert final_chunk.type == "response"
    assert final_chunk.response is not None
    assert final_chunk.response.text == "你好"
    assert final_chunk.response.usage == {"input_tokens": 3, "output_tokens": 2}
