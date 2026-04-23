"""Gateway Server - 多通道统一入口"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from agent.app import AgentApplication
from agent.gateway.adapters import FeishuAdapter
from agent.gateway.command_queue import CommandQueue
from agent.gateway.normalizer import NormalizedMessage
from agent.observability.tracing import elapsed_ms, ensure_trace_id, mark_monotonic, text_preview, trace_fields
from agent.web.events import AssistantTextDeltaEvent, RunErrorEvent, RunFinishedEvent, ToolCallStartEvent

logger = logging.getLogger(__name__)


class GatewayServer:
    """Gateway 网关服务器。
    
    职责:
    - 管理多个通道适配器（CLI/飞书/...）
    - 通过 CommandQueue 保证同一 session 串行执行
    - 提供统一的消息路由和响应机制
    """

    def __init__(self, app: AgentApplication):
        self.app = app
        self.command_queue = CommandQueue(handler=self._handle_message)
        self.adapters: dict[str, any] = {}
        self._running = False
        self._feishu_patch_interval_secs = 0.8

    async def register_feishu(self, app_id: str, app_secret: str) -> None:
        """注册飞书通道适配器"""
        adapter = FeishuAdapter(app_id, app_secret)
        await adapter.connect()
        self.adapters["feishu"] = adapter
        logger.info("Feishu adapter registered")

    async def start(self) -> None:
        """启动 Gateway 服务器"""
        self._running = True
        await self.command_queue.start()

        # 启动所有适配器的消息接收循环
        tasks = []
        for channel, adapter in self.adapters.items():
            task = asyncio.create_task(self._receive_loop(channel, adapter))
            tasks.append(task)

        logger.info(f"Gateway server started with {len(self.adapters)} adapters")

        try:
            # 保持运行直到被停止
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Gateway server shutdown requested")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """停止 Gateway 服务器"""
        self._running = False
        await self.command_queue.stop()
        logger.info("Gateway server stopped")

    async def _receive_loop(self, channel: str, adapter) -> None:
        """单个适配器的消息接收循环"""
        logger.debug(f"Starting receive loop for channel: {channel}")

        try:
            async for message in adapter.receive():
                if not self._running:
                    break

                metadata = message.metadata
                ensure_trace_id(metadata, fallback_id=message.message_id)
                received_at = mark_monotonic(metadata, "gateway_received_at")
                logger.info(
                    f"{trace_fields(metadata, session_id=message.session_id, channel=channel)} "
                    f"event=message_received sender={message.sender} body_chars={len(message.body)} "
                    f"received_ms={elapsed_ms(received_at, end=received_at)} preview={text_preview(message.body)}"
                )
                await self.command_queue.enqueue(message)

        except asyncio.CancelledError:
            logger.debug(f"Receive loop cancelled for channel: {channel}")
        except Exception as e:
            logger.error(f"Receive loop error for channel {channel}: {e}", exc_info=True)

    async def _handle_message(self, message: NormalizedMessage) -> str:
        """处理单条消息（由 CommandQueue 调用）"""
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)
        started_at = mark_monotonic(metadata, "gateway_handler_started_at")

        if message.channel == "feishu":
            return await self._handle_feishu_message(message)

        try:
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=message_started body_chars={len(message.body)}"
            )
            # 调用 AgentCore 处理消息
            result = await self.app.core.run(message)

            # 通过对应通道发送回复
            await self._send_response(message.channel, message.session_id, result)
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=message_completed total_ms={elapsed_ms(started_at)} output_chars={len(result or '')}"
            )

            return result

        except Exception as e:
            error_msg = f"Error processing message: {str(e)}"
            logger.error(error_msg, exc_info=True)

            # 发送错误消息给用户
            await self._send_response(
                message.channel,
                message.session_id,
                f"抱歉，处理您的消息时出错了: {str(e)}",
            )

            raise

    async def _handle_feishu_message(self, message: NormalizedMessage) -> str:
        """飞书通道专用处理：先回执，再更新同一条卡片。"""

        adapter = self.adapters.get("feishu")
        if adapter is None:
            raise RuntimeError("No adapter found for channel: feishu")

        source_message_id = message.metadata.get("source_message_id")
        placeholder_id: str | None = None
        final_text = ""
        last_patch_at = 0.0
        last_patch_status: str | None = None
        last_patch_markdown: str | None = None
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)
        started_at = metadata.get("gateway_handler_started_at") or mark_monotonic(
            metadata, "gateway_handler_started_at"
        )
        streaming_logged = False

        async def patch_placeholder(
            markdown: str,
            *,
            status: str | None = None,
            force: bool = False,
        ) -> None:
            nonlocal last_patch_at, last_patch_status, last_patch_markdown

            if placeholder_id is None:
                return

            content_changed = markdown != last_patch_markdown
            status_changed = status != last_patch_status
            if not force and not content_changed and not status_changed:
                return

            now = asyncio.get_running_loop().time()
            patch_due = (
                force
                or self._feishu_patch_interval_secs <= 0
                or now - last_patch_at >= self._feishu_patch_interval_secs
            )
            if not patch_due:
                return

            await adapter.update_markdown(
                placeholder_id,
                markdown,
                status=status,
            )
            last_patch_at = now
            last_patch_status = status
            last_patch_markdown = markdown

        try:
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=feishu_message_started body_chars={len(message.body)} preview={text_preview(message.body)}"
            )
            if source_message_id:
                placeholder_id = await adapter.reply_markdown(
                    source_message_id,
                    "",
                    status="👀 已收到，正在思考…",
                )
                mark_monotonic(metadata, "feishu_ack_sent_at")
                last_patch_at = asyncio.get_running_loop().time()
                last_patch_status = "👀 已收到，正在思考…"
                last_patch_markdown = ""
                logger.info(
                    f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                    f"event=feishu_ack_sent after_ms={elapsed_ms(started_at)} placeholder_id={placeholder_id}"
                )

            async for event in self.app.core.run_events(message):
                if isinstance(event, AssistantTextDeltaEvent):
                    final_text += event.delta
                    if not streaming_logged:
                        streaming_logged = True
                        mark_monotonic(metadata, "feishu_streaming_started_at")
                        logger.info(
                            f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                            f"event=feishu_streaming_started after_ms={elapsed_ms(started_at)}"
                        )
                    await patch_placeholder(
                        final_text,
                        status="✍️ 正在输出…",
                    )
                elif isinstance(event, ToolCallStartEvent):
                    logger.info(
                        f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                        f"event=feishu_tool_status_updated after_ms={elapsed_ms(started_at)} tool_name={event.tool_call_name}"
                    )
                    await patch_placeholder(
                        final_text,
                        status=f"🛠 正在调用工具：`{event.tool_call_name}`",
                        force=True,
                    )
                elif isinstance(event, RunErrorEvent):
                    raise RuntimeError(event.message)
                elif isinstance(event, RunFinishedEvent):
                    final_text = event.result_text or final_text

            if placeholder_id is not None:
                await patch_placeholder(final_text, force=True)
            else:
                await self._send_response(message.channel, message.session_id, final_text)

            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=feishu_message_completed total_ms={elapsed_ms(started_at)} output_chars={len(final_text or '')}"
            )
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=feishu_timing_summary queue_wait_ms={elapsed_ms(metadata.get('queue_enqueued_at'), end=metadata.get('queue_dequeued_at'))} "
                f"ack_ms={elapsed_ms(started_at, end=metadata.get('feishu_ack_sent_at'))} "
                f"first_visible_token_ms={elapsed_ms(started_at, end=metadata.get('feishu_streaming_started_at'))} "
                f"core_run_ms={elapsed_ms(metadata.get('run_started_at'), end=metadata.get('run_finished_at'))} "
                f"model_total_ms={metadata.get('timing_model_ms_total', 0)} "
                f"tool_exec_ms_total={metadata.get('timing_tool_exec_ms_total', 0)} "
                f"tool_roundtrip_ms_total={metadata.get('timing_tool_roundtrip_ms_total', 0)} "
                f"tool_call_count={metadata.get('timing_tool_call_count', 0)}"
            )
            return final_text

        except Exception as e:
            error_msg = f"抱歉，处理您的消息时出错了: {str(e)}"
            logger.error(f"Error processing Feishu message: {e}", exc_info=True)

            try:
                if placeholder_id is not None:
                    await adapter.update_markdown(placeholder_id, error_msg, status="⚠️ 处理失败")
                else:
                    await self._send_response(message.channel, message.session_id, error_msg)
            except Exception as send_error:
                logger.error(f"Failed to send Feishu error response: {send_error}", exc_info=True)

            raise

    async def _send_response(
        self, channel: str, session_id: str, content: str
    ) -> None:
        """通过指定通道发送响应"""
        adapter = self.adapters.get(channel)
        if not adapter:
            logger.warning(f"No adapter found for channel: {channel}")
            return

        try:
            await adapter.send(session_id, content)
            logger.debug(f"Response sent via {channel} to session {session_id}")
        except Exception as e:
            logger.error(f"Failed to send response via {channel}: {e}", exc_info=True)

    async def send_to_channel(
        self, channel: str, session_id: str, content: str
    ) -> None:
        """主动向某个通道发送消息（用于 Heartbeat/Cron）"""
        await self._send_response(channel, session_id, content)
