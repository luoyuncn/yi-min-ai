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


@pytest.mark.asyncio
async def test_call_stream_passes_extra_body_to_openai_compatible_endpoint(monkeypatch) -> None:
    """兼容 Provider 应透传 provider 级 extra_body 给下游端点。"""

    captured: dict[str, object] = {}

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="qwen",
            provider_type="openai",
            model="qwen3.6-plus",
            api_key_env="OPENAI_API_KEY",
            extra_body={"enable_thinking": False},
        )
    )
    await provider.initialize()

    chunks = [
        chunk
        async for chunk in provider.call_stream(
            LLMRequest(messages=[{"role": "user", "content": "你好"}])
        )
    ]

    assert captured["extra_body"] == {"enable_thinking": False}
    assert chunks[-1].type == "response"
    assert chunks[-1].response is not None
    assert chunks[-1].response.text is None


@pytest.mark.asyncio
async def test_call_stream_passes_deepseek_thinking_config_to_openai_compatible_endpoint(
    monkeypatch,
) -> None:
    """DeepSeek thinking 配置应按官方 extra_body 结构透传。"""

    captured: dict[str, object] = {}

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="deepseek",
            provider_type="openai",
            model="deepseek-v4-pro",
            api_key_env="DEEPSEEK_API_KEY",
            extra_body={"thinking": {"type": "disabled"}},
        )
    )
    await provider.initialize()

    chunks = [
        chunk
        async for chunk in provider.call_stream(
            LLMRequest(messages=[{"role": "user", "content": "你好"}])
        )
    ]

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert chunks[-1].type == "response"
    assert chunks[-1].response is not None
    assert chunks[-1].response.text is None


@pytest.mark.asyncio
async def test_call_stream_logs_provider_request_config_for_thinking_mode(monkeypatch, caplog) -> None:
    """兼容 Provider 应记录关键请求配置，便于确认思考模式是否关闭。"""

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    caplog.set_level("INFO", logger="agent.providers.openai_compat")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="qwen",
            provider_type="openai",
            model="qwen3.6-plus",
            api_key_env="OPENAI_API_KEY",
            extra_body={"enable_thinking": False},
        )
    )
    await provider.initialize()

    async for _ in provider.call_stream(LLMRequest(messages=[{"role": "user", "content": "你好"}])):
        pass

    assert "event=provider_request_config" in caplog.text
    assert "enable_thinking=False" in caplog.text


@pytest.mark.asyncio
async def test_call_stream_logs_provider_request_config_for_deepseek_thinking_type(
    monkeypatch, caplog
) -> None:
    """兼容 Provider 应记录 DeepSeek thinking.type，便于排查参数是否生效。"""

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            return FakeStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    caplog.set_level("INFO", logger="agent.providers.openai_compat")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="deepseek",
            provider_type="openai",
            model="deepseek-v4-pro",
            api_key_env="DEEPSEEK_API_KEY",
            extra_body={"thinking": {"type": "disabled"}},
        )
    )
    await provider.initialize()

    async for _ in provider.call_stream(LLMRequest(messages=[{"role": "user", "content": "你好"}])):
        pass

    assert "event=provider_request_config" in caplog.text
    assert "thinking_type=disabled" in caplog.text


@pytest.mark.asyncio
async def test_call_stream_passes_common_generation_parameters_to_openai_endpoint(monkeypatch) -> None:
    """兼容 Provider 应透传公共生成参数，并允许请求级覆盖。"""

    captured: dict[str, object] = {}

    class FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return FakeStream()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai_compat_module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAICompatProvider(
        ProviderConfig(
            name="gpt-5",
            provider_type="openai",
            model="gpt-5.4",
            api_key_env="OPENAI_API_KEY",
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=3000,
        )
    )
    await provider.initialize()

    async for _ in provider.call_stream(
        LLMRequest(
            messages=[{"role": "user", "content": "你好"}],
            temperature=0.6,
        )
    ):
        pass

    assert captured["temperature"] == 0.6
    assert captured["top_p"] == 0.8
    assert captured["max_tokens"] == 3000
