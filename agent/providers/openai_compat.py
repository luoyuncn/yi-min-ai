"""OpenAI 兼容 Provider 适配器。

支持任何实现了 OpenAI Chat Completions 接口的服务：
OpenAI、Azure OpenAI、DeepSeek、Ollama、vLLM 等。

通过 `base_url` 指向目标服务，`api_key_env` 指定密钥所在的环境变量名。
"""

import json
import logging
import os
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from openai import AsyncOpenAI

from agent.core.provider import LLMProvider, LLMRequest, LLMResponse, LLMStreamChunk

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """OpenAI Chat Completions 兼容协议的适配器。"""

    def __init__(self, config) -> None:
        super().__init__(config)
        self._client: AsyncOpenAI | None = None

    async def initialize(self) -> None:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise ValueError(f"Missing API key: {self.config.api_key_env}")
        kwargs = {"api_key": api_key, "max_retries": 0, "timeout": 30.0}
        if self.config.base_url:
            kwargs["base_url"] = self._normalize_base_url(self.config.base_url)
        self._client = AsyncOpenAI(**kwargs)

    async def call(self, request: LLMRequest) -> LLMResponse:
        kwargs = self._build_request_kwargs(request)
        self._log_request_config(kwargs, stream=False)
        response = await self._client.chat.completions.create(**kwargs)
        return self._convert_response(response)

    async def call_stream(self, request: LLMRequest):
        """执行 OpenAI Chat Completions 流式调用。"""

        kwargs = self._build_request_kwargs(request)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        self._log_request_config(kwargs, stream=True)
        stream_started_at = monotonic()
        stream = await self._client.chat.completions.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, object]] = {}
        usage: dict[str, int] = {}
        chunk_count = 0
        content_chunks = 0
        tool_call_chunks = 0
        reasoning_chunks = 0
        reasoning_chars = 0
        empty_choice_chunks = 0
        first_chunk_ms: int | None = None
        first_content_ms: int | None = None
        last_content_ms: int | None = None
        max_chunk_gap_ms = 0
        previous_chunk_at: float | None = None

        async for chunk in stream:
            chunk_count += 1
            now = monotonic()
            elapsed = int((now - stream_started_at) * 1000)
            if first_chunk_ms is None:
                first_chunk_ms = elapsed
            if previous_chunk_at is not None:
                max_chunk_gap_ms = max(max_chunk_gap_ms, int((now - previous_chunk_at) * 1000))
            previous_chunk_at = now

            if getattr(chunk, "usage", None):
                usage = {
                    "input_tokens": chunk.usage.prompt_tokens,
                    "output_tokens": chunk.usage.completion_tokens,
                }

            if not getattr(chunk, "choices", None):
                empty_choice_chunks += 1
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            reasoning_content = getattr(delta, "reasoning_content", None)
            if reasoning_content:
                reasoning_chunks += 1
                reasoning_chars += len(reasoning_content)

            if delta.content:
                content_chunks += 1
                last_content_ms = elapsed
                if first_content_ms is None:
                    first_content_ms = elapsed
                text_parts.append(delta.content)
                yield LLMStreamChunk(type="text_delta", delta=delta.content)

            for tool_call in delta.tool_calls or []:
                tool_call_chunks += 1
                current = tool_calls.setdefault(
                    tool_call.index,
                    {
                        "id": tool_call.id or f"tool-call-{tool_call.index}",
                        "name": "",
                        "arguments_parts": [],
                    },
                )
                if tool_call.id:
                    current["id"] = tool_call.id

                function = tool_call.function
                if function is None:
                    continue
                if function.name:
                    current["name"] = function.name
                if function.arguments:
                    current["arguments_parts"].append(function.arguments)

        converted_tool_calls = [
            {
                "id": item["id"],
                "name": item["name"],
                "input": json.loads("".join(item["arguments_parts"]) or "{}"),
            }
            for _, item in sorted(tool_calls.items())
        ]
        response_text = "".join(text_parts) or None
        response_type = "tool_calls" if converted_tool_calls else "text"
        total_ms = int((monotonic() - stream_started_at) * 1000)
        logger.info(
            "event=provider_stream_summary "
            f"provider={self.config.name} model={self.config.model} total_ms={total_ms} "
            f"chunks={chunk_count} content_chunks={content_chunks} tool_call_chunks={tool_call_chunks} "
            f"empty_choice_chunks={empty_choice_chunks} reasoning_chunks={reasoning_chunks} "
            f"reasoning_chars={reasoning_chars} content_chars={len(response_text or '')} "
            f"first_chunk_ms={first_chunk_ms if first_chunk_ms is not None else -1} "
            f"first_content_ms={first_content_ms if first_content_ms is not None else -1} "
            f"last_content_ms={last_content_ms if last_content_ms is not None else -1} "
            f"max_chunk_gap_ms={max_chunk_gap_ms} "
            f"input_tokens={usage.get('input_tokens', -1)} output_tokens={usage.get('output_tokens', -1)}"
        )
        yield LLMStreamChunk(
            type="response",
            response=LLMResponse(
                type=response_type,
                text=response_text,
                tool_calls=converted_tool_calls or None,
                provider=self.config.name,
                model=self.config.model,
                usage=usage,
            ),
        )

    def _build_request_kwargs(self, request: LLMRequest) -> dict:
        kwargs = {
            "model": self.config.model,
            "messages": self._convert_messages(request.messages),
            "max_tokens": request.max_tokens or self.config.max_output_tokens,
        }
        temperature = request.temperature if request.temperature is not None else self.config.temperature
        if temperature is not None:
            kwargs["temperature"] = temperature

        top_p = request.top_p if request.top_p is not None else self.config.top_p
        if top_p is not None:
            kwargs["top_p"] = top_p

        if self.config.extra_body:
            kwargs["extra_body"] = dict(self.config.extra_body)
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)
            kwargs["tool_choice"] = "auto"
        return kwargs

    def _log_request_config(self, kwargs: dict, *, stream: bool) -> None:
        """记录关键请求配置，便于排查兼容端点行为。"""

        extra_body = kwargs.get("extra_body") or {}
        enable_thinking = extra_body.get("enable_thinking")
        thinking = extra_body.get("thinking") or {}
        thinking_type = thinking.get("type") if isinstance(thinking, dict) else None
        logger.info(
            "event=provider_request_config "
            f"provider={self.config.name} model={self.config.model} stream={stream} "
            f"message_count={len(kwargs.get('messages', []))} tool_count={len(kwargs.get('tools', []))} "
            f"message_chars={_json_char_count(kwargs.get('messages', []))} "
            f"tool_schema_chars={_json_char_count(kwargs.get('tools', []))} "
            f"max_tokens={kwargs.get('max_tokens')} temperature={kwargs.get('temperature')} "
            f"top_p={kwargs.get('top_p')} enable_thinking={enable_thinking} "
            f"thinking_type={thinking_type} "
            f"base_url={self.config.base_url or 'default'}"
        )

    def _convert_messages(self, messages: list[dict]) -> list[dict]:
        """把内部消息格式转换成 OpenAI messages 格式。

        内部格式与 OpenAI 格式非常接近，主要差异在 tool_calls 的结构上。
        """
        converted = []
        for message in messages:
            role = message["role"]

            if role == "assistant" and message.get("tool_calls"):
                tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"], ensure_ascii=False),
                        },
                    }
                    for tc in message["tool_calls"]
                ]
                converted.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or None,
                        "tool_calls": tool_calls,
                    }
                )
                continue

            if role == "tool":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )
                continue

            converted.append({"role": role, "content": message["content"]})

        return converted

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """把统一 schema 转换成 OpenAI 工具定义格式。"""
        return [
            {
                "type": "function",
                "function": tool["function"],
            }
            for tool in tools
        ]

    def _convert_response(self, response) -> LLMResponse:
        """把 OpenAI 响应转换成统一 LLMResponse。"""
        if isinstance(response, str):
            stripped = response.lstrip().lower()
            if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
                raise ValueError(
                    "OpenAI-compatible endpoint returned HTML instead of JSON. "
                    "Check `base_url`; many gateways require a `/v1` suffix."
                )
            return LLMResponse(
                type="text",
                text=response,
                provider=self.config.name,
                model=self.config.model,
            )

        choice = response.choices[0]
        message = choice.message

        text = message.content or None
        tool_calls: list[dict] = []

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": json.loads(tc.function.arguments),
                    }
                )

        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        response_type = "tool_calls" if tool_calls else "text"
        return LLMResponse(
            type=response_type,
            text=text,
            tool_calls=tool_calls or None,
            provider=self.config.name,
            model=self.config.model,
            usage=usage,
        )

    def _normalize_base_url(self, base_url: str) -> str:
        """把 bare host 形式的兼容端点规范成 OpenAI SDK 可用的 API 根路径。"""

        parsed = urlsplit(base_url)
        if parsed.path not in {"", "/"}:
            return base_url.rstrip("/")

        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", parsed.query, parsed.fragment))


def _json_char_count(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except TypeError:
        return -1
