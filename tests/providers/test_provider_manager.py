"""ProviderManager 测试。

这里不测真实网络调用，只验证抽象层的注册和转发行为。
"""

import pytest

from agent.core.provider import LLMRequest, LLMResponse, ProviderConfig
from agent.core.provider_manager import ProviderManager


class FakeProvider:
    """测试用假 Provider，用来隔离真实外部依赖。"""

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


@pytest.mark.asyncio
async def test_provider_manager_calls_registered_primary_provider() -> None:
    """注册的主 Provider 应该收到调用并返回统一响应。"""

    config = ProviderConfig(
        name="claude-sonnet",
        provider_type="anthropic",
        model="claude-sonnet-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
    )
    manager = ProviderManager(provider_factories={"anthropic": FakeProvider})
    await manager.register(config, make_primary=True)

    response = await manager.call(LLMRequest(messages=[{"role": "user", "content": "ping"}]))

    assert response.text == "pong"
    assert response.provider == "claude-sonnet"
