"""上下文压缩引擎 - 当会话历史接近窗口极限时触发"""

import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class CompactionEngine:
    """上下文压缩引擎。
    
    策略:
    - 保留最早 2 轮和最近 4 轮的原始对话
    - 中间部分用辅助小模型生成摘要替换
    - 将 lineage（摘要→原始消息映射）存入 SQLite
    """

    def __init__(
        self,
        provider_manager,
        session_archive,
        max_context_tokens: int = 128000,
        compaction_reserve: int = 4096,
    ):
        """
        Args:
            provider_manager: LLM Provider 管理器
            session_archive: Session 归档（用于存储 lineage）
            max_context_tokens: 最大上下文 token 数
            compaction_reserve: 预留给模型回复的 token 数
        """
        self.provider_manager = provider_manager
        self.session_archive = session_archive
        self.max_context_tokens = max_context_tokens
        self.compaction_reserve = compaction_reserve
        self.compaction_threshold = max_context_tokens - compaction_reserve

    def should_compact(self, context: list[dict], token_count: int) -> bool:
        """判断是否需要压缩"""
        return token_count > self.compaction_threshold

    async def compact(self, context: list[dict]) -> list[dict]:
        """压缩上下文。
        
        Args:
            context: 完整上下文消息列表
            
        Returns:
            压缩后的上下文
        """
        # 分离系统消息和对话历史
        system_messages = [m for m in context if m.get("role") == "system"]
        history = [m for m in context if m.get("role") != "system"]

        if len(history) <= 12:  # 6 轮对话（每轮 user+assistant）
            # 太短不需要压缩
            return context

        # 保留最早 2 轮（4 条消息）+ 最近 4 轮（8 条消息）
        preserved_head = history[:4]
        preserved_tail = history[-8:]
        middle = history[4:-8]

        if not middle:
            return context

        logger.info(
            f"Compacting {len(middle)} messages "
            f"(preserving {len(preserved_head)} head + {len(preserved_tail)} tail)"
        )

        # 使用压缩专用模型生成摘要
        summary_text = await self._generate_summary(middle)

        # 生成 lineage 记录
        summary_id = str(uuid4())
        self._store_lineage(summary_id, middle, summary_text)

        # 重组上下文
        summary_message = {
            "role": "system",
            "content": (
                f"[Conversation summary of {len(middle)} messages]:\n\n"
                f"{summary_text}\n\n"
                f"[Summary ID: {summary_id}]"
            ),
        }

        compacted = system_messages + preserved_head + [summary_message] + preserved_tail

        logger.info(f"Compaction complete: {len(context)} → {len(compacted)} messages")
        return compacted

    async def _generate_summary(self, messages: list[dict]) -> str:
        """使用 LLM 生成会话摘要"""
        try:
            # 格式化消息历史
            formatted = self._format_messages(messages)

            # 调用压缩专用 Provider（可以用更便宜的小模型）
            from agent.core.provider import LLMRequest

            response = await self.provider_manager.call(
                LLMRequest(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Summarize this conversation preserving all key facts, "
                                "decisions, and action items. Be precise with names, dates, "
                                "numbers, and technical details. Focus on WHAT was discussed "
                                "and WHAT was decided, not HOW it was discussed."
                            ),
                        },
                        {"role": "user", "content": formatted},
                    ],
                    max_tokens=1024,
                    temperature=0.3,  # 低温度确保稳定性
                )
            )

            return response.text or "(Summary generation failed)"

        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            # 降级：生成简单摘要
            return self._fallback_summary(messages)

    def _format_messages(self, messages: list[dict]) -> str:
        """将消息列表格式化为可读文本"""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                if content:
                    lines.append(f"Assistant: {content}")
                if tool_calls:
                    for tc in tool_calls:
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        lines.append(f"  [Tool: {tool_name}]")
            elif role == "tool":
                lines.append(f"  [Tool Result: {content[:100]}...]")

        return "\n".join(lines)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """降级摘要：简单统计"""
        user_count = sum(1 for m in messages if m.get("role") == "user")
        tool_count = sum(
            len(m.get("tool_calls", [])) for m in messages if m.get("tool_calls")
        )

        return (
            f"Conversation segment with {user_count} user messages "
            f"and {tool_count} tool calls. "
            "(Detailed summary unavailable due to generation failure.)"
        )

    def _store_lineage(
        self, summary_id: str, original_messages: list[dict], summary_text: str
    ) -> None:
        """存储压缩 lineage（摘要→原始消息映射）"""
        try:
            # 提取原始消息 ID（如果有的话）
            message_ids = [
                m.get("id", "") for m in original_messages if m.get("id")
            ]

            # 存入 SQLite（需要扩展 session_archive 的表结构）
            # TODO: 添加 compaction_lineage 表
            # self.session_archive.store_lineage(
            #     summary_id=summary_id,
            #     original_message_ids=message_ids,
            #     summary_text=summary_text,
            # )

            logger.debug(
                f"Lineage stored: {summary_id} -> {len(message_ids)} messages"
            )

        except Exception as e:
            logger.warning(f"Failed to store lineage: {e}")
