"""Provider 管理器。

一期只做最基础的事情：
1. 注册 Provider
2. 指定一个 primary provider
3. 把请求转发给 primary provider

后续多 Provider fallback、健康检查等能力都可以在这里继续扩展。
"""

from typing import Any, AsyncIterator

from agent.core.provider import LLMRequest, LLMResponse, LLMStreamChunk, ProviderConfig
from agent.providers.anthropic import AnthropicProvider
from agent.providers.openai_compat import OpenAICompatProvider


class ProviderManager:
    """统一管理已注册的 Provider。"""

    def __init__(self, provider_factories: dict[str, type] | None = None) -> None:
        self._provider_factories = provider_factories or {
            "anthropic": AnthropicProvider,
            "openai": OpenAICompatProvider,
        }
        self._providers: dict[str, Any] = {}
        self._primary: str | None = None

    async def register(self, config: ProviderConfig, make_primary: bool = False) -> None:
        """实例化并注册一个 Provider。"""

        provider_factory = self._provider_factories.get(config.provider_type)
        if provider_factory is None:
            raise ValueError(f"Unsupported provider type: {config.provider_type}")

        provider = provider_factory(config)
        await provider.initialize()
        self._providers[config.name] = provider

        if make_primary or self._primary is None:
            self._primary = config.name

    async def call(self, request: LLMRequest) -> LLMResponse:
        """把请求转发给当前 primary provider。"""

        if self._primary is None:
            raise RuntimeError("No primary provider is registered")

        provider = self._providers[self._primary]
        return await provider.call(request)

    async def call_stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """把请求转发给当前 primary provider 的流式接口。"""

        if self._primary is None:
            raise RuntimeError("No primary provider is registered")

        provider = self._providers[self._primary]
        call_stream = getattr(provider, "call_stream", None)
        if call_stream is None:
            yield LLMStreamChunk(type="response", response=await provider.call(request))
            return

        async for chunk in call_stream(request):
            yield chunk
