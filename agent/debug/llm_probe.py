"""LLM 诊断脚本。

使用当前项目的 LLMFactory + ProviderManager 链路创建真实 Provider，
并输出运行时配置、首 token 延迟、总耗时与最终响应内容。
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

from agent.app import _load_environment_files
from agent.config import load_settings
from agent.core.llm_factory import LLMFactory
from agent.core.provider import LLMRequest
from agent.core.provider_manager import ProviderManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProbeResult:
    """一次探测调用的结果。"""

    response_type: str
    text: str | None
    usage: dict[str, int]
    first_token_seconds: float | None
    total_seconds: float
    tool_calls_count: int


def build_probe_config(
    settings,
    *,
    provider_name: str | None,
    thinking: str,
    temperature: float | None = None,
    top_p: float | None = None,
    max_output_tokens: int | None = None,
):
    """根据 Settings 和诊断参数构造运行时 ProviderConfig。"""

    enable_thinking = _resolve_thinking_override(thinking)
    return LLMFactory.create_primary(
        settings,
        provider_name=provider_name,
        enable_thinking=enable_thinking,
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
    )


def build_probe_request(
    prompt: str,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
) -> LLMRequest:
    """构造一次纯文本探测请求。"""

    return LLMRequest(
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )


async def probe_manager(
    manager,
    request: LLMRequest,
    *,
    clock: Callable[[], float] = perf_counter,
    writer: Callable[[str], None] | None = None,
) -> ProbeResult:
    """对 ProviderManager 发起一次探测并记录延迟。"""

    started_at = clock()
    first_token_seconds: float | None = None
    delta_parts: list[str] = []
    final_response = None

    async for chunk in manager.call_stream(request):
        if chunk.type == "text_delta" and chunk.delta:
            delta_parts.append(chunk.delta)
            if first_token_seconds is None:
                first_token_seconds = clock() - started_at
            if writer is not None:
                writer(chunk.delta)
            continue

        if chunk.type == "response" and chunk.response is not None:
            final_response = chunk.response

    total_seconds = clock() - started_at
    text = None
    usage: dict[str, int] = {}
    response_type = "unknown"
    tool_calls_count = 0

    if final_response is not None:
        response_type = final_response.type
        text = final_response.text or "".join(delta_parts) or None
        usage = final_response.usage
        tool_calls_count = len(final_response.tool_calls or [])
    elif delta_parts:
        response_type = "text"
        text = "".join(delta_parts)

    return ProbeResult(
        response_type=response_type,
        text=text,
        usage=usage,
        first_token_seconds=first_token_seconds,
        total_seconds=total_seconds,
        tool_calls_count=tool_calls_count,
    )


def _resolve_thinking_override(thinking: str) -> bool | None:
    if thinking == "on":
        return True
    if thinking == "off":
        return False
    if thinking == "auto":
        return None
    raise ValueError(f"Unsupported thinking mode: {thinking}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe the current LLM pipeline and measure latency with thinking toggled.",
    )
    parser.add_argument(
        "--config",
        default="config/agent.yaml",
        help="Path to agent.yaml. Default: config/agent.yaml",
    )
    parser.add_argument(
        "--provider",
        help="Optional provider name override. Default: settings.providers.default_primary",
    )
    parser.add_argument(
        "--prompt",
        default="请只回复 OK",
        help="Prompt sent to the model. Default: 请只回复 OK",
    )
    parser.add_argument(
        "--thinking",
        choices=["auto", "on", "off"],
        default="off",
        help="Thinking override applied through LLMFactory. Default: off",
    )
    parser.add_argument("--temperature", type=float, help="Runtime provider temperature override")
    parser.add_argument("--top-p", dest="top_p", type=float, help="Runtime provider top_p override")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        help="Runtime provider max_output_tokens override",
    )
    parser.add_argument(
        "--request-temperature",
        type=float,
        help="Per-request temperature override",
    )
    parser.add_argument(
        "--request-top-p",
        dest="request_top_p",
        type=float,
        help="Per-request top_p override",
    )
    parser.add_argument(
        "--request-max-tokens",
        type=int,
        help="Per-request max_tokens override",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Do not print streamed deltas while waiting for the final response",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level. Default: INFO",
    )
    return parser


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(levelname)s:%(name)s:%(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def _serialize_runtime_config(runtime_config) -> dict:
    return {
        "name": runtime_config.name,
        "provider_type": runtime_config.provider_type,
        "model": runtime_config.model,
        "base_url": runtime_config.base_url,
        "temperature": runtime_config.temperature,
        "top_p": runtime_config.top_p,
        "max_output_tokens": runtime_config.max_output_tokens,
        "extra_body": runtime_config.extra_body,
    }


def _serialize_request(request: LLMRequest) -> dict:
    return {
        "messages": request.messages,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "max_tokens": request.max_tokens,
    }


def _print_section(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main_async(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    config_path = Path(args.config).resolve()
    _load_environment_files(config_path)
    settings = load_settings(config_path)

    runtime_config = build_probe_config(
        settings,
        provider_name=args.provider,
        thinking=args.thinking,
        temperature=args.temperature,
        top_p=args.top_p,
        max_output_tokens=args.max_output_tokens,
    )
    request = build_probe_request(
        args.prompt,
        temperature=args.request_temperature,
        top_p=args.request_top_p,
        max_tokens=args.request_max_tokens,
    )

    _print_section("Runtime Config", _serialize_runtime_config(runtime_config))
    _print_section("Request", _serialize_request(request))

    manager = ProviderManager()
    await manager.register(runtime_config, make_primary=True)

    stream_used = False

    def _write_delta(text: str) -> None:
        nonlocal stream_used
        stream_used = True
        print(text, end="", flush=True)

    if not args.no_stream:
        print("\n=== Streaming Output ===")

    result = await probe_manager(
        manager,
        request,
        writer=None if args.no_stream else _write_delta,
    )

    if stream_used:
        print()

    _print_section(
        "Result",
        {
            "response_type": result.response_type,
            "first_token_seconds": result.first_token_seconds,
            "total_seconds": result.total_seconds,
            "tool_calls_count": result.tool_calls_count,
            "usage": result.usage,
        },
    )

    if result.text and (args.no_stream or not stream_used):
        print("\n=== Response Text ===")
        print(result.text)

    if result.response_type != "text":
        logger.warning(
            "Probe returned non-text response_type=%s tool_calls_count=%s",
            result.response_type,
            result.tool_calls_count,
        )

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
