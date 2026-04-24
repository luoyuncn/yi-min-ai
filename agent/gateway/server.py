"""Gateway Server - 多通道统一入口"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from agent.app import AgentApplication
from agent.gateway.adapters import FeishuAdapter
from agent.gateway.command_queue import CommandQueue
from agent.gateway.feishu_cards import FeishuCardRenderer
from agent.gateway.normalizer import NormalizedMessage, build_thread_key
from agent.observability.tracing import elapsed_ms, ensure_trace_id, mark_monotonic, text_preview, trace_fields
from agent.web.events import (
    AssistantTextDeltaEvent,
    RunErrorEvent,
    RunFinishedEvent,
    ToolCallArgsEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

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
        self.runtime_apps: dict[str, AgentApplication] = {"default": app}
        self.command_queue = CommandQueue(handler=self._handle_message)
        self.adapters: dict[str, any] = {}
        self._running = False
        self._feishu_patch_interval_secs = 0.8
        self._feishu_card_renderers: dict[str, FeishuCardRenderer] = {}

    def register_runtime_app(self, runtime_id: str, app: AgentApplication) -> None:
        """注册一个 runtime 对应的 AgentApplication。"""

        self.runtime_apps[runtime_id] = app

    async def register_feishu(
        self,
        app_id: str,
        app_secret: str,
        *,
        adapter_id: str = "feishu",
    ) -> None:
        """注册飞书通道适配器"""
        adapter = FeishuAdapter(app_id, app_secret, adapter_id=adapter_id)
        await adapter.connect()
        self.adapters[adapter_id] = adapter
        logger.info("Feishu adapter registered: %s", adapter_id)

    async def start(self) -> None:
        """启动 Gateway 服务器"""
        self._running = True
        await self.command_queue.start()

        # 启动所有适配器的消息接收循环
        tasks = []
        for adapter_id, adapter in self.adapters.items():
            task = asyncio.create_task(self._receive_loop(adapter_id, adapter))
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

    async def _receive_loop(self, adapter_id: str, adapter) -> None:
        """单个适配器的消息接收循环"""
        logger.debug(f"Starting receive loop for adapter: {adapter_id}")

        try:
            async for message in adapter.receive():
                if not self._running:
                    break

                metadata = message.metadata
                ensure_trace_id(metadata, fallback_id=message.message_id)
                received_at = mark_monotonic(metadata, "gateway_received_at")
                logger.info(
                    f"{trace_fields(metadata, session_id=message.thread_key, channel=message.channel)} "
                    f"event=message_received sender={message.sender} body_chars={len(message.body)} "
                    f"received_ms={elapsed_ms(received_at, end=received_at)} preview={text_preview(message.body)} "
                    f"channel_instance={message.channel_instance}"
                )
                runtime_app = self._resolve_runtime_app(message.channel_instance)
                if not self._reserve_inbound_message(runtime_app, message):
                    logger.info(
                        f"{trace_fields(metadata, session_id=message.thread_key, channel=message.channel)} "
                        f"event=message_duplicate_dropped channel_message_id={message.message_id} "
                        f"channel_instance={message.channel_instance}"
                    )
                    continue
                await self.command_queue.enqueue(message)

        except asyncio.CancelledError:
            logger.debug(f"Receive loop cancelled for adapter: {adapter_id}")
        except Exception as e:
            logger.error(f"Receive loop error for adapter {adapter_id}: {e}", exc_info=True)

    async def _handle_message(self, message: NormalizedMessage) -> str:
        """处理单条消息（由 CommandQueue 调用）"""
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)
        started_at = mark_monotonic(metadata, "gateway_handler_started_at")
        runtime_app = self._resolve_runtime_app(message.channel_instance)
        self._mark_inbound_message_status(runtime_app, message, status="processing")

        if message.channel == "feishu":
            return await self._handle_feishu_message(message, runtime_app)

        try:
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=message_started body_chars={len(message.body)}"
            )
            # 调用 AgentCore 处理消息
            result = await runtime_app.core.run(message)

            # 通过对应通道发送回复
            await self._send_response(
                message.channel,
                message.session_id,
                result,
                channel_instance=message.channel_instance,
                runtime_app=runtime_app,
                run_id=message.message_id,
                reply_to_channel_message_id=message.metadata.get("source_message_id"),
                caused_by_message_id=message.message_id,
                thread_key=message.thread_key,
            )
            self._mark_inbound_message_status(runtime_app, message, status="completed")
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
                channel_instance=message.channel_instance,
                runtime_app=runtime_app,
                run_id=message.message_id,
                reply_to_channel_message_id=message.metadata.get("source_message_id"),
                caused_by_message_id=message.message_id,
                thread_key=message.thread_key,
            )
            self._mark_inbound_message_status(
                runtime_app,
                message,
                status="failed",
                error_message=str(e),
            )

            raise

    async def _handle_feishu_message(self, message: NormalizedMessage, runtime_app: AgentApplication) -> str:
        """飞书通道专用处理：先回执，再更新同一条卡片。"""

        adapter = self._resolve_adapter(message.channel, message.channel_instance)
        if adapter is None:
            raise RuntimeError(f"No adapter found for channel: {message.channel}/{message.channel_instance}")

        renderer = self._resolve_feishu_card_renderer(message.channel_instance, runtime_app)
        source_message_id = message.metadata.get("source_message_id")
        placeholder_id: str | None = None
        final_text = ""
        last_patch_at = 0.0
        last_patch_status: str | None = None
        last_patch_card: dict | None = None
        metadata = message.metadata
        ensure_trace_id(metadata, fallback_id=message.message_id)
        started_at = metadata.get("gateway_handler_started_at") or mark_monotonic(
            metadata, "gateway_handler_started_at"
        )
        streaming_logged = False
        tool_trace_by_id: dict[str, dict] = {}
        tool_call_args_buffer: dict[str, str] = {}

        async def patch_placeholder(
            assistant_text: str,
            *,
            status: str | None = None,
            force: bool = False,
        ) -> None:
            nonlocal last_patch_at, last_patch_status, last_patch_card

            if placeholder_id is None:
                return

            card = renderer.render_placeholder_card(
                user_text=message.body,
                assistant_text=assistant_text,
                status=status,
            )
            content_changed = card != last_patch_card
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

            await adapter.update_card(
                placeholder_id,
                card,
            )
            self._record_outbound_message(
                runtime_app=runtime_app,
                channel=message.channel,
                channel_instance=message.channel_instance,
                session_id=message.session_id,
                thread_key=message.thread_key,
                channel_message_id=placeholder_id,
                reply_to_channel_message_id=source_message_id,
                caused_by_message_id=message.message_id,
                run_id=message.message_id,
                content=assistant_text,
                status=status or "updated",
                payload={"message_type": "interactive", "source": "feishu_patch", "card": card},
            )
            last_patch_at = now
            last_patch_status = status
            last_patch_card = card

        try:
            logger.info(
                f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                f"event=feishu_message_started body_chars={len(message.body)} preview={text_preview(message.body)}"
            )
            if source_message_id:
                ack_card = renderer.render_placeholder_card(
                    user_text=message.body,
                    status="👀 已收到，正在思考…",
                )
                placeholder_id = await adapter.reply_card(
                    source_message_id,
                    ack_card,
                )
                mark_monotonic(metadata, "feishu_ack_sent_at")
                last_patch_at = asyncio.get_running_loop().time()
                last_patch_status = "👀 已收到，正在思考…"
                last_patch_card = ack_card
                self._record_outbound_message(
                    runtime_app=runtime_app,
                    channel=message.channel,
                    channel_instance=message.channel_instance,
                    session_id=message.session_id,
                    thread_key=message.thread_key,
                    channel_message_id=placeholder_id,
                    reply_to_channel_message_id=source_message_id,
                    caused_by_message_id=message.message_id,
                    run_id=message.message_id,
                    content="",
                    status="ack_sent",
                    payload={"message_type": "interactive", "source": "feishu_reply", "card": ack_card},
                )
                logger.info(
                    f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                    f"event=feishu_ack_sent after_ms={elapsed_ms(started_at)} placeholder_id={placeholder_id}"
                )

            async for event in runtime_app.core.run_events(message):
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
                    tool_trace_by_id.setdefault(
                        event.tool_call_id,
                        {"tool_name": event.tool_call_name, "input": None, "result": None},
                    )
                    logger.info(
                        f"{trace_fields(metadata, session_id=message.session_id, channel=message.channel, run_id=message.message_id)} "
                        f"event=feishu_tool_status_updated after_ms={elapsed_ms(started_at)} tool_name={event.tool_call_name}"
                    )
                    await patch_placeholder(
                        final_text,
                        status=f"🛠 正在处理：{event.tool_call_name}",
                        force=True,
                    )
                elif isinstance(event, ToolCallArgsEvent):
                    buffer = tool_call_args_buffer.get(event.tool_call_id, "") + (event.delta or "")
                    tool_call_args_buffer[event.tool_call_id] = buffer
                    parsed_args = self._try_parse_json_dict(buffer)
                    if parsed_args is not None:
                        tool_trace_by_id.setdefault(
                            event.tool_call_id,
                            {"tool_name": "", "input": None, "result": None},
                        )["input"] = parsed_args
                elif isinstance(event, ToolCallResultEvent):
                    tool_trace_by_id.setdefault(
                        event.tool_call_id,
                        {"tool_name": "", "input": None, "result": None},
                    )["result"] = event.content
                elif isinstance(event, RunErrorEvent):
                    raise RuntimeError(event.message)
                elif isinstance(event, RunFinishedEvent):
                    final_text = event.result_text or final_text

            if placeholder_id is not None:
                final_card = renderer.render_final_card(
                    user_text=message.body,
                    assistant_text=final_text,
                    tool_calls=self._serialize_tool_traces(tool_trace_by_id),
                    tool_results=self._serialize_tool_results(tool_trace_by_id),
                )
                await adapter.update_card(placeholder_id, final_card)
                self._record_outbound_message(
                    runtime_app=runtime_app,
                    channel=message.channel,
                    channel_instance=message.channel_instance,
                    session_id=message.session_id,
                    thread_key=message.thread_key,
                    channel_message_id=placeholder_id,
                    reply_to_channel_message_id=source_message_id,
                    caused_by_message_id=message.message_id,
                    run_id=message.message_id,
                    content=final_text,
                    status="completed",
                    payload={"message_type": "interactive", "source": "feishu_final", "card": final_card},
                )
            else:
                await self._send_response(
                    message.channel,
                    message.session_id,
                    final_text,
                    channel_instance=message.channel_instance,
                    runtime_app=runtime_app,
                    run_id=message.message_id,
                    reply_to_channel_message_id=source_message_id,
                    caused_by_message_id=message.message_id,
                    thread_key=message.thread_key,
                )

            self._mark_inbound_message_status(runtime_app, message, status="completed")
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
                    error_card = renderer.render_error_card(
                        user_text=message.body,
                        error_text=error_msg,
                    )
                    await adapter.update_card(placeholder_id, error_card)
                    self._record_outbound_message(
                        runtime_app=runtime_app,
                        channel=message.channel,
                        channel_instance=message.channel_instance,
                        session_id=message.session_id,
                        thread_key=message.thread_key,
                        channel_message_id=placeholder_id,
                        reply_to_channel_message_id=source_message_id,
                        caused_by_message_id=message.message_id,
                        run_id=message.message_id,
                        content=error_msg,
                        status="failed",
                        payload={"message_type": "interactive", "source": "feishu_error", "card": error_card},
                    )
                else:
                    await self._send_response(
                        message.channel,
                        message.session_id,
                        error_msg,
                        channel_instance=message.channel_instance,
                        runtime_app=runtime_app,
                        run_id=message.message_id,
                        reply_to_channel_message_id=source_message_id,
                        caused_by_message_id=message.message_id,
                        thread_key=message.thread_key,
                    )
            except Exception as send_error:
                logger.error(f"Failed to send Feishu error response: {send_error}", exc_info=True)

            self._mark_inbound_message_status(
                runtime_app,
                message,
                status="failed",
                error_message=str(e),
            )
            raise

    async def _send_response(
        self,
        channel: str,
        session_id: str,
        content: str,
        *,
        channel_instance: str = "default",
        runtime_app: AgentApplication | None = None,
        run_id: str | None = None,
        reply_to_channel_message_id: str | None = None,
        caused_by_message_id: str | None = None,
        thread_key: str | None = None,
    ) -> str | None:
        """通过指定通道发送响应"""
        adapter = self._resolve_adapter(channel, channel_instance)
        if not adapter:
            logger.warning(f"No adapter found for channel: {channel}/{channel_instance}")
            return None

        try:
            payload = {"message_type": "text"}
            if channel == "feishu" and runtime_app is not None and hasattr(adapter, "send_card"):
                renderer = self._resolve_feishu_card_renderer(channel_instance, runtime_app)
                card = renderer.render_final_card(
                    user_text="",
                    assistant_text=content,
                    tool_calls=[],
                    tool_results=[],
                )
                outbound_message_id = await adapter.send_card(session_id, card)
                payload = {"message_type": "interactive", "source": "feishu_send", "card": card}
            else:
                outbound_message_id = await adapter.send(session_id, content)
            if runtime_app is not None:
                outbound_status = "sent"
                if channel == "feishu" and outbound_message_id is None:
                    outbound_status = "send_failed"
                self._record_outbound_message(
                    runtime_app=runtime_app,
                    channel=channel,
                    channel_instance=channel_instance,
                    session_id=session_id,
                    thread_key=thread_key or build_thread_key(
                        session_id,
                        channel=channel,
                        channel_instance=channel_instance,
                    ),
                    channel_message_id=outbound_message_id,
                    reply_to_channel_message_id=reply_to_channel_message_id,
                    caused_by_message_id=caused_by_message_id,
                    run_id=run_id,
                    content=content,
                    status=outbound_status,
                    payload=payload,
                )
            logger.debug(f"Response sent via {channel}/{channel_instance} to session {session_id}")
            return outbound_message_id
        except Exception as e:
            logger.error(f"Failed to send response via {channel}: {e}", exc_info=True)
            return None

    async def send_to_channel(
        self,
        channel: str,
        session_id: str,
        content: str,
        *,
        channel_instance: str = "default",
    ) -> None:
        """主动向某个通道发送消息（用于 Heartbeat/Cron）"""
        runtime_app = self._resolve_runtime_app(channel_instance)
        await self._send_response(
            channel,
            session_id,
            content,
            channel_instance=channel_instance,
            runtime_app=runtime_app,
            thread_key=build_thread_key(
                session_id,
                channel=channel,
                channel_instance=channel_instance,
            ),
        )

    def _resolve_runtime_app(self, channel_instance: str) -> AgentApplication:
        runtime_app = self.runtime_apps.get(channel_instance) or self.runtime_apps.get("default")
        if runtime_app is None:
            raise RuntimeError(f"No runtime app registered for channel instance: {channel_instance}")
        return runtime_app

    def _resolve_adapter(self, channel: str, channel_instance: str):
        adapter = self.adapters.get(channel_instance)
        if adapter is not None:
            return adapter

        adapter = self.adapters.get(channel)
        if adapter is not None:
            return adapter

        matching = [
            candidate
            for candidate in self.adapters.values()
            if getattr(candidate, "channel_type", None) == channel
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    def _reserve_inbound_message(self, runtime_app: AgentApplication, message: NormalizedMessage) -> bool:
        archive = getattr(runtime_app.core, "session_archive", None)
        if archive is None:
            return True

        return archive.reserve_inbound_message(
            channel=message.channel,
            channel_instance=message.channel_instance,
            channel_message_id=message.message_id,
            session_id=message.session_id,
            thread_key=message.thread_key,
            sender=message.sender,
            content=message.body,
            run_id=message.message_id,
            payload=message.metadata,
        )

    def _mark_inbound_message_status(
        self,
        runtime_app: AgentApplication,
        message: NormalizedMessage,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        archive = getattr(runtime_app.core, "session_archive", None)
        if archive is None:
            return

        archive.mark_channel_message_status(
            channel=message.channel,
            channel_instance=message.channel_instance,
            direction="inbound",
            channel_message_id=message.message_id,
            status=status,
            run_id=message.message_id,
            error_message=error_message,
        )

    def _record_outbound_message(
        self,
        *,
        runtime_app: AgentApplication,
        channel: str,
        channel_instance: str,
        session_id: str,
        thread_key: str,
        content: str,
        status: str,
        channel_message_id: str | None = None,
        reply_to_channel_message_id: str | None = None,
        caused_by_message_id: str | None = None,
        run_id: str | None = None,
        payload: dict | None = None,
    ) -> str:
        archive = getattr(runtime_app.core, "session_archive", None)
        if archive is None:
            return ""

        return archive.upsert_channel_message(
            direction="outbound",
            role="assistant",
            channel=channel,
            channel_instance=channel_instance,
            session_id=session_id,
            thread_key=thread_key,
            channel_message_id=channel_message_id,
            reply_to_channel_message_id=reply_to_channel_message_id,
            caused_by_message_id=caused_by_message_id,
            run_id=run_id,
            content=content,
            status=status,
            payload=payload,
        )

    def _resolve_feishu_card_renderer(
        self,
        channel_instance: str,
        runtime_app: AgentApplication,
    ) -> FeishuCardRenderer:
        renderer = self._feishu_card_renderers.get(channel_instance)
        if renderer is not None:
            return renderer

        system_prompt = getattr(getattr(runtime_app.core, "context_assembler", None), "system_prompt", "")
        first_line = (system_prompt.splitlines()[0].strip() if system_prompt else "") or "You are Yi Min."
        agent_name = "Yi Min"
        if first_line.lower().startswith("you are "):
            agent_name = first_line[8:].strip().rstrip(".") or "Yi Min"

        renderer = FeishuCardRenderer(agent_name=agent_name)
        self._feishu_card_renderers[channel_instance] = renderer
        return renderer

    def _try_parse_json_dict(self, raw: str) -> dict | None:
        import json

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _serialize_tool_traces(self, tool_trace_by_id: dict[str, dict]) -> list[dict]:
        return [
            {
                "tool_name": trace.get("tool_name", ""),
                "input": trace.get("input"),
            }
            for trace in tool_trace_by_id.values()
        ]

    def _serialize_tool_results(self, tool_trace_by_id: dict[str, dict]) -> list[dict]:
        return [
            {
                "tool_name": trace.get("tool_name", ""),
                "input": trace.get("input"),
                "content": trace.get("result") or "",
            }
            for trace in tool_trace_by_id.values()
            if trace.get("result") is not None
        ]
