"""Anthropic Provider 适配器。

这个文件的核心作用是做“协议翻译”：
上层使用统一的 `LLMRequest / LLMResponse`，
这里把它们转换成 Anthropic SDK 需要的格式，再把响应转回来。
"""

import os

from anthropic import AsyncAnthropic

from agent.core.provider import LLMProvider, LLMRequest, LLMResponse, LLMStreamChunk


class AnthropicProvider(LLMProvider):
    """Anthropic SDK 的一期实现。"""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._client: AsyncAnthropic | None = None

    async def initialize(self) -> None:
        """创建 Anthropic 异步客户端。"""

        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise ValueError(f"Missing API key: {self.config.api_key_env}")
        self._client = AsyncAnthropic(api_key=api_key)

    async def call(self, request: LLMRequest) -> LLMResponse:
        """执行一次 Anthropic 非流式调用。"""

        kwargs = self._build_request_kwargs(request)
        response = await self._client.messages.create(**kwargs)
        return self._convert_response(response)

    async def call_stream(self, request: LLMRequest):
        """执行 Anthropic 流式调用，并把文本增量逐段透传。"""

        kwargs = self._build_request_kwargs(request)
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                if text:
                    yield LLMStreamChunk(type="text_delta", delta=text)
            response = await stream.get_final_message()

        yield LLMStreamChunk(type="response", response=self._convert_response(response))

    def _build_request_kwargs(self, request: LLMRequest) -> dict:
        system_prompt, messages = self._convert_messages(request.messages)
        kwargs = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": request.max_tokens or self.config.max_output_tokens,
        }
        temperature = request.temperature if request.temperature is not None else self.config.temperature
        if temperature is not None:
            kwargs["temperature"] = temperature

        top_p = request.top_p if request.top_p is not None else self.config.top_p
        if top_p is not None:
            kwargs["top_p"] = top_p

        if system_prompt:
            kwargs["system"] = system_prompt
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)
        return kwargs

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """把内部消息格式转换成 Anthropic messages API 所需格式。"""

        system_parts: list[str] = []
        converted: list[dict] = []

        for message in messages:
            role = message["role"]
            if role == "system":
                system_parts.append(message["content"])
                continue

            if role == "assistant" and message.get("tool_calls"):
                # 当 assistant 想调用工具时，需要转换成 Anthropic 的 `tool_use` block。
                content_blocks = []
                if message.get("content"):
                    content_blocks.append({"type": "text", "text": message["content"]})
                for tool_call in message["tool_calls"]:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call["id"],
                            "name": tool_call["name"],
                            "input": tool_call["input"],
                        }
                    )
                converted.append({"role": "assistant", "content": content_blocks})
                continue

            if role == "tool":
                # 工具结果在 Anthropic 协议里要包装成 user 侧的 `tool_result` block。
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message["tool_call_id"],
                                "content": message["content"],
                            }
                        ],
                    }
                )
                continue

            converted.append({"role": role, "content": message["content"]})

        return "\n\n".join(system_parts), converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """把统一 schema 转换成 Anthropic 的工具定义格式。"""

        converted = []
        for tool in tools:
            function = tool["function"]
            converted.append(
                {
                    "name": function["name"],
                    "description": function["description"],
                    "input_schema": function["parameters"],
                }
            )
        return converted

    def _convert_response(self, response) -> LLMResponse:
        """把 Anthropic 响应还原成统一的 LLMResponse。"""

        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            if block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )

        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        response_type = "tool_calls" if tool_calls else "text"
        text = "\n".join(part for part in text_parts if part) or None
        return LLMResponse(
            type=response_type,
            text=text,
            tool_calls=tool_calls or None,
            provider=self.config.name,
            model=self.config.model,
            usage=usage,
        )
