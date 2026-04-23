"""模型调用抽象层的数据结构与接口。

这里把“上层希望怎么调用模型”抽象成统一的数据模型，
避免核心循环直接依赖某家 SDK 的参数形状。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass(slots=True)
class ProviderConfig:
    """单个 Provider 的运行时配置。"""

    name: str
    provider_type: str
    model: str
    api_key_env: str
    base_url: str | None = None
    max_output_tokens: int = 2048


@dataclass(slots=True)
class LLMRequest:
    """发给模型的一次请求。"""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int | None = None


@dataclass(slots=True)
class LLMResponse:
    """模型返回的统一结果。

    一期只区分两类：
    - `text`：直接回复
    - `tool_calls`：要求继续执行工具
    """

    type: str
    text: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    provider: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class LLMStreamChunk:
    """流式调用过程中产生的增量块。"""

    type: str
    delta: str | None = None
    response: LLMResponse | None = None


class LLMProvider(ABC):
    """所有 Provider 实现都要遵守的最小接口。"""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the provider client."""

    @abstractmethod
    async def call(self, request: LLMRequest) -> LLMResponse:
        """Execute a non-streaming model call."""

    async def call_stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Execute a streaming model call when supported.

        默认退化成一次性返回完整响应，方便旧实现逐步升级。
        """

        yield LLMStreamChunk(type="response", response=await self.call(request))
