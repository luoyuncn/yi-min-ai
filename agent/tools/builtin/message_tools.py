"""主动发消息给用户的工具。

Agent 想主动推送通知、提醒或任何内容给用户时调用。
消息通过 GatewayServer.send_to_channel() 发出，默认发到当前会话。
"""

import asyncio
import json
import logging

from agent.tools.runtime_context import RuntimeServices, RuntimeToolContext

logger = logging.getLogger(__name__)


def message_send(
    services: RuntimeServices,
    *,
    context: RuntimeToolContext,
    content: str,
    session_id: str | None = None,
    channel_instance: str | None = None,
) -> str:
    """主动向用户发送一条消息。

    Args:
        content: 要发送的消息内容（纯文本或 Markdown）。
        session_id: 目标会话 ID，默认为当前对话会话。
        channel_instance: 渠道实例，默认为当前渠道实例。
    """
    gateway = getattr(services, "gateway", None)
    if gateway is None:
        return json.dumps(
            {"error": "gateway 不可用，无法主动发消息（仅 Feishu 渠道支持）"},
            ensure_ascii=False,
        )

    target_session = session_id or context.session_id
    target_instance = channel_instance or context.channel_instance
    target_channel = context.channel

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            gateway.send_to_channel(
                target_channel,
                target_session,
                content,
                channel_instance=target_instance,
            )
        )
        logger.info(
            "event=proactive_message_queued channel=%s session=%s instance=%s chars=%d",
            target_channel,
            target_session,
            target_instance,
            len(content),
        )
        return json.dumps(
            {
                "status": "ok",
                "channel": target_channel,
                "session_id": target_session,
                "channel_instance": target_instance,
                "content_chars": len(content),
            },
            ensure_ascii=False,
        )
    except RuntimeError as exc:
        return json.dumps({"error": f"发送失败：{exc}"}, ensure_ascii=False)
