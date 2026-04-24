"""飞书通道适配器 - 基于 lark-oapi WebSocket 长连接"""

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime
from typing import AsyncIterator

from agent.gateway.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)


class FeishuAdapter:
    """飞书通道适配器 - 基于 lark-oapi WebSocket 长连接。
    
    核心特性:
    - 使用 lark-oapi SDK 的 WebSocket 模式（无需公网 IP）
    - 自动处理鉴权和重连
    - 支持群聊和私聊
    - 支持富文本消息卡片
    """

    def __init__(self, app_id: str, app_secret: str, *, adapter_id: str = "feishu"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.adapter_id = adapter_id
        self.channel_type = "feishu"
        self._message_queue: asyncio.Queue[NormalizedMessage] = asyncio.Queue()
        self._lark_client = None
        self._ws_client = None
        self._ws_loop = None
        self._initialized = False
        self._ws_connected = threading.Event()
        self._ws_connect_timeout_secs = 15.0

        # 检查 lark-oapi 是否已安装
        try:
            import lark_oapi as lark
            self._lark = lark
        except ImportError:
            raise RuntimeError(
                "lark-oapi not installed. Install with: pip install lark-oapi"
            )

    async def connect(self) -> None:
        """建立 WebSocket 长连接"""
        if self._initialized:
            logger.warning("FeishuAdapter already connected")
            return

        if not hasattr(self, "_ws_connected"):
            self._ws_connected = threading.Event()
        if not hasattr(self, "_ws_connect_timeout_secs"):
            self._ws_connect_timeout_secs = 15.0

        logger.info("Connecting to Feishu via WebSocket...")

        # 构建事件处理器
        event_handler = (
            self._lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

        # 建立 WebSocket 长连接（SDK 内置鉴权 + 重连）
        self._ws_client = self._lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=self._lark.LogLevel.INFO,
        )

        # 构建 API Client 用于主动发消息
        self._lark_client = (
            self._lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )

        # 在后台线程启动 WebSocket
        # 注意：lark-oapi SDK 会在模块级缓存一个 loop，这里需要显式切到线程自己的 loop。
        self._ws_thread_started = threading.Event()
        self._ws_thread_error = None
        self._ws_connected.clear()

        def _start_ws():
            """在后台线程中运行 WebSocket 客户端"""
            try:
                self._run_ws_client()
            except Exception as e:
                self._ws_thread_error = e
                logger.error(f"Feishu WebSocket thread error: {e}", exc_info=True)
            finally:
                self._ws_thread_started.set()

        # 启动后台线程
        ws_thread = threading.Thread(
            target=_start_ws,
            daemon=True,
            name="FeishuWebSocket"
        )
        ws_thread.start()

        # 等待连接建立或出错
        connected = await asyncio.to_thread(
            self._ws_connected.wait,
            self._ws_connect_timeout_secs,
        )

        if self._ws_thread_error:
            raise RuntimeError(f"Failed to connect to Feishu: {self._ws_thread_error}")
        if not connected:
            raise RuntimeError(
                f"Failed to connect to Feishu: timed out after {self._ws_connect_timeout_secs:.1f}s"
            )

        self._initialized = True
        logger.info("Feishu WebSocket connected")

    def _run_ws_client(self) -> None:
        """在线程内为 lark WebSocket 客户端绑定独立事件循环。"""

        ws_module = getattr(self._lark.ws, "client", None)
        previous_loop = getattr(ws_module, "loop", None) if ws_module is not None else None
        ws_loop = asyncio.new_event_loop()
        self._ws_loop = ws_loop

        try:
            asyncio.set_event_loop(ws_loop)
            if ws_module is not None:
                ws_module.loop = ws_loop
            logger.info("Feishu WebSocket thread loop created")
            ws_loop.run_until_complete(self._ws_client._connect())
            self._ws_connected.set()
            logger.info("Feishu WebSocket handshake completed")
            ws_loop.create_task(self._ws_client._ping_loop())
            ws_loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(ws_loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                try:
                    ws_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                except Exception:
                    pass
            if ws_module is not None and previous_loop is not None:
                ws_module.loop = previous_loop
            self._ws_loop = None
            asyncio.set_event_loop(None)
            ws_loop.close()

    def _on_message_receive(self, data) -> None:
        """飞书消息事件回调 → 标准化 → 放入队列"""
        try:
            event = data.event
            message = event.message

            # 仅处理文本消息（一期），后续扩展图片/文件/语音
            if message.message_type != "text":
                logger.debug(f"Ignoring non-text message: {message.message_type}")
                return

            content = json.loads(message.content)
            text = content.get("text", "")

            # 群聊中仅响应 @机器人 的消息
            if message.chat_type == "group":
                mentions = getattr(message, "mentions", [])
                if not any(m.get("id", {}).get("union_id") == self.app_id for m in mentions):
                    logger.debug("Ignoring group message without mention")
                    return
                # 移除 @机器人 的文本
                text = text.replace(f"@{self.app_id}", "").strip()

            normalized = NormalizedMessage(
                message_id=message.message_id,
                session_id=message.chat_id,
                sender=event.sender.sender_id.open_id,
                body=text,
                attachments=[],
                channel="feishu",
                channel_instance=getattr(self, "adapter_id", "feishu"),
                metadata={
                    "chat_type": message.chat_type,
                    "mentions": getattr(message, "mentions", []),
                    "source_message_id": message.message_id,
                    "adapter_id": getattr(self, "adapter_id", "feishu"),
                },
                timestamp=datetime.fromtimestamp(
                    int(message.create_time) / 1000, tz=UTC
                ),
            )

            self._message_queue.put_nowait(normalized)
            logger.debug(f"Feishu message queued: {message.message_id}")

        except Exception as e:
            logger.error(f"Failed to process Feishu message: {e}")

    async def receive(self) -> AsyncIterator[NormalizedMessage]:
        """接收消息流"""
        while True:
            msg = await self._message_queue.get()
            yield msg

    async def send(self, session_id: str, content: str) -> None:
        """通过飞书 API 回复消息"""
        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        try:
            request = (
                self._lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    self._lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(session_id)
                    .msg_type("text")
                    .content(json.dumps({"text": content}, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self._lark_client.im.v1.message.create(request)
            
            if not response.success():
                logger.error(
                    f"Failed to send Feishu message: {response.code} {response.msg}"
                )
            else:
                logger.debug(f"Feishu message sent to {session_id}")

        except Exception as e:
            logger.error(f"Error sending Feishu message: {e}")

    async def reply_markdown(
        self,
        source_message_id: str,
        markdown: str,
        *,
        status: str | None = None,
    ) -> str:
        """回复一条可渲染 Markdown 的卡片消息，并返回新消息 ID。"""

        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        content = json.dumps(self._build_markdown_card(markdown, status=status), ensure_ascii=False)
        request = (
            self._lark.im.v1.ReplyMessageRequest.builder()
            .message_id(source_message_id)
            .request_body(
                self._lark.im.v1.ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type("interactive")
                .reply_in_thread(False)
                .build()
            )
            .build()
        )

        response = self._lark_client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"Failed to reply Feishu message: {response.code} {response.msg}")

        return response.data.message_id

    async def update_markdown(
        self,
        message_id: str,
        markdown: str,
        *,
        status: str | None = None,
    ) -> None:
        """把一条已发送的飞书卡片更新为最新 Markdown 内容。"""

        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        content = json.dumps(self._build_markdown_card(markdown, status=status), ensure_ascii=False)
        request = (
            self._lark.im.v1.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                self._lark.im.v1.PatchMessageRequestBody.builder()
                .content(content)
                .build()
            )
            .build()
        )

        response = self._lark_client.im.v1.message.patch(request)
        if not response.success():
            raise RuntimeError(f"Failed to update Feishu message: {response.code} {response.msg}")

    async def send_rich(self, session_id: str, blocks: list[dict]) -> None:
        """发送富文本/消息卡片（飞书 Interactive Card）"""
        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        try:
            card_content = self._build_card(blocks)

            request = (
                self._lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    self._lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(session_id)
                    .msg_type("interactive")
                    .content(json.dumps(card_content, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self._lark_client.im.v1.message.create(request)
            
            if not response.success():
                logger.error(
                    f"Failed to send Feishu card: {response.code} {response.msg}"
                )
            else:
                logger.debug(f"Feishu card sent to {session_id}")

        except Exception as e:
            logger.error(f"Error sending Feishu card: {e}")

    def _build_card(self, blocks: list[dict]) -> dict:
        """构建飞书消息卡片 JSON"""
        return {
            "config": {"wide_screen_mode": True},
            "elements": blocks,
        }

    def _build_markdown_card(self, markdown: str, *, status: str | None = None) -> dict:
        """构建支持 lark_md 的简单卡片。"""

        sections: list[str] = []
        if status:
            sections.append(status)
        if markdown:
            sections.append(markdown)

        content = "\n\n".join(part for part in sections if part).strip() or " "
        return self._build_card(
            [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content,
                    },
                }
            ]
        )
