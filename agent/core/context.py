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
        #
        # 注意：下面这些分区标题和说明都会直接暴露给 LLM。
        # 标题用中文是为了减少中英夹杂；文件名、工具名仍保留原样，
        # 因为它们同时是代码和 function calling 的稳定接口。
        current_time = self.now_provider().astimezone()
        system_time_block = "\n".join(
            [
                "[系统时间]",
                f"当前本地时间 ISO：{current_time.isoformat()}",
                f"当前本地时间：{current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                f"当前本地日期：{current_time.strftime('%Y-%m-%d')}",
                f"时区：{current_time.tzinfo}",
            ]
        )
        channel_block_lines = [
            "[渠道上下文]",
            f"当前渠道：{channel}/{channel_instance}",
        ]
        if channel == "feishu":
            channel_block_lines.append("避免使用 Markdown 表格，优先使用短段落和平铺项目列表。")
            channel_block_lines.append("在飞书卡片里保持格式稳定，不要依赖表格渲染。")
            channel_block_lines.append("工具调用成功后简洁回复，不要叙述隐藏推理过程。")
        channel_block = "\n".join(channel_block_lines)
        reminder_policy_block = "\n".join(
            [
                "[提醒路由]",
                "一次性提醒、闹钟和相对时间提醒使用 `reminder_create`。",
                "只有每天、每周、每月等周期性日程才使用 cron 工具。",
                "相对时间提醒应传入 `delay_seconds`，不要自行计算 cron 表达式。",
                "提醒创建成功后，用一句简短确认回复，并包含具体执行时间。",
            ]
        )
        human_block_lines = [
            "[用户上下文]",
            f"当前发送者：{sender or 'unknown'}",
            f"聊天类型：{(metadata or {}).get('chat_type', 'unknown')}",
        ]
        human_block = "\n".join(human_block_lines)
        memory_items_block = "\n".join(
            [
                "[检索到的长期记忆]",
                memory_items_text.strip() or "本轮没有检索到长期记忆项。",
            ]
        )
        identity_source_block = "\n".join(
            [
                "[身份事实来源]",
                "当前 `SOUL.md` 是助手活跃身份、名称、人格和风格的权威来源。",
                "如果聊天历史、笔记、工具载荷或旧记忆与 `SOUL.md` 冲突，以 `SOUL.md` 为准。",
            ]
        )
        system_content = "\n\n".join(
            [
                self.system_prompt,
                system_time_block,
                channel_block,
                human_block,
                identity_source_block,
                "[SOUL.md]",
                soul_text,
                "[PROFILE.md]",
                memory_text,
                memory_items_block,
                reminder_policy_block,
                "[工具索引]",
                tool_index,
                "[技能索引]",
                skill_index,
            ]
        )
        return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": user_message}]
