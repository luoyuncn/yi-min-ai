"""上下文组装器。

这里的职责只有一个：
把系统提示、Always-On Memory、Skill 索引、会话历史和当前用户输入，
整理成一次模型调用所需的 `messages` 列表。
"""

from datetime import datetime
from typing import Callable

import tiktoken


class ContextAssembler:
    """负责拼装一次调用的模型上下文。"""

    def __init__(
        self,
        system_prompt: str,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.now_provider = now_provider or datetime.now
        self._tokenizer = None

    def count_tokens(self, text: str) -> int:
        """计算文本的 token 数量（使用 tiktoken）"""
        if self._tokenizer is None:
            try:
                self._tokenizer = tiktoken.get_encoding("cl100k_base")
            except Exception:
                # 降级：粗略估计（1 token ≈ 4 字符）
                return len(text) // 4

        return len(self._tokenizer.encode(text))

    def count_context_tokens(self, context: list[dict]) -> int:
        """计算整个上下文的 token 数量"""
        total = 0
        for msg in context:
            content = msg.get("content", "")
            total += self.count_tokens(content)

            # 如果有工具调用，也计入
            if msg.get("tool_calls"):
                import json
                total += self.count_tokens(json.dumps(msg["tool_calls"]))

        return total

    def assemble(
        self,
        soul_text: str,
        memory_text: str,
        tool_index: str,
        skill_index: str,
        history: list[dict],
        user_message: str,
        *,
        channel: str = "cli",
        channel_instance: str = "default",
        sender: str | None = None,
        metadata: dict | None = None,
        memory_items_text: str = "",
    ) -> list[dict]:
        """把所有上下文片段按固定顺序组装起来。"""

        # 系统层内容被收敛成一条大的 system message，
        # 这样模型每次调用都能稳定拿到人格、长期记忆和技能索引。
        current_time = self.now_provider().astimezone()
        system_time_block = "\n".join(
            [
                "[SYSTEM TIME]",
                f"Current local datetime ISO: {current_time.isoformat()}",
                f"Current local datetime: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                f"Current local date: {current_time.strftime('%Y-%m-%d')}",
                f"Timezone: {current_time.tzinfo}",
            ]
        )
        channel_block_lines = [
            "[CHANNEL CONTEXT]",
            f"Current channel: {channel}/{channel_instance}",
        ]
        if channel == "feishu":
            channel_block_lines.append("Avoid Markdown tables. Prefer short paragraphs and flat bullet lists.")
            channel_block_lines.append("Keep formatting stable in Feishu cards. Do not rely on table rendering.")
            channel_block_lines.append("Reply concisely after successful tool calls; do not narrate hidden reasoning.")
        channel_block = "\n".join(channel_block_lines)
        reminder_policy_block = "\n".join(
            [
                "[REMINDER ROUTING]",
                "Use reminder_create for one-shot reminders, alarms, and relative reminders.",
                "Use cron tools only for recurring schedules such as daily, weekly, or monthly tasks.",
                "For relative reminders, pass delay_seconds instead of calculating a cron expression.",
                "After a reminder is created, reply with one short confirmation including the due time.",
            ]
        )
        human_block_lines = [
            "[HUMAN CONTEXT]",
            f"Current sender: {sender or 'unknown'}",
            f"Chat type: {(metadata or {}).get('chat_type', 'unknown')}",
        ]
        human_block = "\n".join(human_block_lines)
        memory_items_block = "\n".join(
            [
                "[MEMORY ITEMS]",
                memory_items_text.strip() or "No retrieved durable memory items.",
            ]
        )
        system_content = "\n\n".join(
            [
                self.system_prompt,
                system_time_block,
                channel_block,
                human_block,
                "[SOUL.md]",
                soul_text,
                "[PROFILE.md]",
                memory_text,
                memory_items_block,
                reminder_policy_block,
                "[TOOL INDEX]",
                tool_index,
                "[SKILL INDEX]",
                skill_index,
            ]
        )
        return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": user_message}]
