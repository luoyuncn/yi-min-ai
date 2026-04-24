"""飞书通道适配器 - 基于 lark-oapi WebSocket 长连接"""

import asyncio
import json
import logging
import re
import threading
import time
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
        self._seen_message_ids: dict[str, float] = {}
        self._seen_message_ids_lock = threading.Lock()
        self._message_dedupe_ttl_secs = 120.0

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

        # 构建事件处理器（不产生 Future，可在主 loop 上构建）
        self._event_handler = (
            self._lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

        # 注意：ws.Client 必须在 _run_ws_client() 的线程 loop 上创建，
        # 否则其内部 connection_lost_waiter Future 会绑定到主 loop，
        # 导致 _receive_message_loop 里 asyncio.shield() 抛 "attached to a different loop"。

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

            # 在线程 loop 上创建 ws.Client，确保其内部 Future（connection_lost_waiter）
            # 绑定到 ws_loop，而不是主 loop，避免 asyncio.shield() 跨 loop 的 RuntimeError。
            self._ws_client = self._lark.ws.Client(
                self.app_id,
                self.app_secret,
                event_handler=self._event_handler,
                log_level=self._lark.LogLevel.INFO,
            )

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

            if self._is_duplicate_message(message.message_id):
                logger.info("Ignoring duplicate Feishu message: %s", message.message_id)
                return

            content = self._parse_message_content(message.content)
            text = self._extract_message_text(message.message_type, content)
            if not text.strip():
                logger.info("Ignoring unsupported Feishu message type: %s", message.message_type)
                return

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

    def _parse_message_content(self, raw_content: str):
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            return {"text": raw_content}

    def _extract_message_text(self, message_type: str, content) -> str:
        if message_type == "text" and isinstance(content, dict):
            return str(content.get("text", "") or "")
        if message_type == "post":
            return self._extract_post_text(content)
        if isinstance(content, dict):
            return str(content.get("text", "") or "")
        return ""

    def _extract_post_text(self, content) -> str:
        if not isinstance(content, dict):
            return ""

        for locale_payload in content.values():
            if not isinstance(locale_payload, dict):
                continue

            lines: list[str] = []
            title = str(locale_payload.get("title", "") or "").strip()
            if title:
                lines.append(title)

            for paragraph in locale_payload.get("content", []) or []:
                if not isinstance(paragraph, list):
                    continue
                parts = [
                    segment
                    for segment in (self._extract_post_segment(item) for item in paragraph)
                    if segment
                ]
                if parts:
                    lines.append("".join(parts))

            if lines:
                return "\n".join(lines)

        return ""

    def _extract_post_segment(self, item) -> str:
        if not isinstance(item, dict):
            return ""

        tag = item.get("tag")
        if tag == "text":
            return str(item.get("text", "") or "")
        if tag == "a":
            text = str(item.get("text", "") or "").strip()
            href = str(item.get("href", "") or "").strip()
            if text and href and text != href:
                return f"{text} ({href})"
            return text or href
        if tag == "at":
            return str(item.get("user_name", "") or item.get("text", "") or "")
        return ""

    def _is_duplicate_message(self, message_id: str) -> bool:
        """在短窗口内按飞书 message_id 去重，避免重复消费同一条消息。"""

        if not hasattr(self, "_seen_message_ids"):
            self._seen_message_ids = {}
        if not hasattr(self, "_seen_message_ids_lock"):
            self._seen_message_ids_lock = threading.Lock()
        if not hasattr(self, "_message_dedupe_ttl_secs"):
            self._message_dedupe_ttl_secs = 120.0

        now = time.monotonic()
        expires_at = now + self._message_dedupe_ttl_secs

        with self._seen_message_ids_lock:
            expired_ids = [
                seen_message_id
                for seen_message_id, deadline in self._seen_message_ids.items()
                if deadline <= now
            ]
            for seen_message_id in expired_ids:
                self._seen_message_ids.pop(seen_message_id, None)

            existing_deadline = self._seen_message_ids.get(message_id)
            if existing_deadline is not None and existing_deadline > now:
                return True

            self._seen_message_ids[message_id] = expires_at
            return False

    async def receive(self) -> AsyncIterator[NormalizedMessage]:
        """接收消息流"""
        while True:
            msg = await self._message_queue.get()
            yield msg

    async def send(self, session_id: str, content: str) -> str | None:
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
                return None
            else:
                logger.debug(f"Feishu message sent to {session_id}")
                return getattr(getattr(response, "data", None), "message_id", None)

        except Exception as e:
            logger.error(f"Error sending Feishu message: {e}")
            return None

    async def send_card(self, session_id: str, card: dict) -> str | None:
        """向会话直接发送一张结构化卡片。"""

        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        try:
            request = (
                self._lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    self._lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(session_id)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )

            response = self._lark_client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    f"Failed to send Feishu card: {response.code} {response.msg}"
                )
                return None

            logger.debug(f"Feishu card sent to {session_id}")
            return getattr(getattr(response, "data", None), "message_id", None)

        except Exception as e:
            logger.error(f"Error sending Feishu card: {e}")
            return None

    async def reply_markdown(
        self,
        source_message_id: str,
        markdown: str,
        *,
        status: str | None = None,
    ) -> str:
        """回复一条可渲染 Markdown 的卡片消息，并返回新消息 ID。"""

        return await self.reply_card(
            source_message_id,
            self._build_markdown_card(markdown, status=status),
        )

    async def reply_card(self, source_message_id: str, card: dict) -> str:
        """回复一条结构化飞书卡片，并返回新消息 ID。"""

        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        content = json.dumps(card, ensure_ascii=False)
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

        await self.update_card(
            message_id,
            self._build_markdown_card(markdown, status=status),
        )

    async def update_card(self, message_id: str, card: dict) -> None:
        """把一条已发送的飞书卡片更新为新的结构化内容。"""

        if not self._lark_client:
            raise RuntimeError("Feishu client not initialized")

        content = json.dumps(card, ensure_ascii=False)
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
            await self.send_card(session_id, self._build_card(blocks))

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
            sections.append(self._normalize_markdown_for_card(markdown))

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

    def _normalize_markdown_for_card(self, markdown: str) -> str:
        """把飞书卡片不稳定的 Markdown 结构降级为更稳的文本。"""

        lines = markdown.splitlines()
        if not lines:
            return markdown

        normalized: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if "|" in line and self._is_markdown_table_separator(next_line):
                headers = self._split_table_row(line)
                index += 2
                converted_rows: list[str] = []
                while index < len(lines) and "|" in lines[index]:
                    cells = self._split_table_row(lines[index])
                    if len(cells) == len(headers) and cells:
                        converted_rows.append(
                            "- " + "；".join(f"{header}：{value}" for header, value in zip(headers, cells))
                        )
                    else:
                        converted_rows.append(lines[index])
                    index += 1
                normalized.extend(converted_rows)
                continue

            normalized.append(line)
            index += 1

        return "\n".join(normalized)

    def _is_markdown_table_separator(self, line: str) -> bool:
        return bool(re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*", line))

    def _split_table_row(self, line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]
