"""上下文组装器。

这里的职责只有一个：
把系统提示、Always-On Memory、Skill 索引、会话历史和当前用户输入，
整理成一次模型调用所需的 `messages` 列表。
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

_CST = timezone(timedelta(hours=8))  # 中国标准时间，永远 UTC+8，无夏令时

import tiktoken


class ContextAssembler:
    """负责拼装一次调用的模型上下文。"""

    def __init__(
        self,
        system_prompt: str,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.now_provider = now_provider or (lambda: datetime.now(_CST))
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
        active_skill_content: str = "",
    ) -> list[dict]:
        """把所有上下文片段按固定顺序组装起来。"""

        # 系统层内容被收敛成一条大的 system message，
        # 这样模型每次调用都能稳定拿到人格、长期记忆和技能索引。
        #
        # 注意：下面这些分区标题和说明都会直接暴露给 LLM。
        # 标题用中文是为了减少中英夹杂；文件名、工具名仍保留原样，
        # 因为它们同时是代码和 function calling 的稳定接口。
        current_time = self.now_provider()
        system_time_block = "\n".join(
            [
                "[系统时间]",
                f"当前时间：{current_time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"当前日期：{current_time.strftime('%Y-%m-%d')}",
                f"星期：{['一', '二', '三', '四', '五', '六', '日'][current_time.weekday()]}",
            ]
        )
        channel_block_lines = [
            "[渠道上下文]",
            f"当前渠道：{channel}/{channel_instance}",
        ]
        if channel == "feishu":
            channel_block_lines.append("【飞书简洁原则】整体回复严格控制在 300 字以内。")
            channel_block_lines.append("剧情叙事仅限 1-2 句话，不展开长段落，不做逐动作分析。")
            channel_block_lines.append("直接给出结论和下一步行动，省略过程描写和评估背景。")
            channel_block_lines.append("工具调用成功后一句话确认即可，不重复工具返回的内容。")
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
        memory_items_value = (memory_items_text or "").strip()
        if memory_items_value:
            memory_items_block = "\n".join(
                [
                    "[检索到的长期记忆]",
                    "以下条目是本轮已经成功检索到的长期记忆，可直接视为当前有效事实。",
                    "当用户问到相关身份、关系、偏好、经历或既有事实时，优先依据这些条目直接回答，不要否认它们存在。",
                    "若同一事实同时出现中英文或近似重复表述，回答时优先用更自然的中文整合，不要机械复述。",
                    memory_items_value,
                ]
            )
        else:
            memory_items_block = "\n".join(
                [
                    "[检索到的长期记忆]",
                    "本轮没有检索到长期记忆项。",
                ]
            )
        identity_source_block = "\n".join(
            [
                "[行为优先级]",
                "当某个技能（SKILL.md）处于激活状态时，技能定义的角色、风格和行为规则优先于 SOUL.md。",
                "SOUL.md 仅在无激活技能时提供默认行为基线；不得用 SOUL.md 里的人格覆盖技能的角色定义。",
            ]
        )
        system_content = "\n\n".join(
            [
                self.system_prompt,
                system_time_block,
                channel_block,
                human_block,
                identity_source_block,
                "[PROFILE.md]",
                memory_text,
                memory_items_block,
                reminder_policy_block,
                "[技能索引]",
                skill_index,
                "[SOUL.md]",
                soul_text,
            ]
        )
        if active_skill_content:
            system_content = system_content + "\n\n[当前活跃技能]\n" + active_skill_content
        return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": user_message}]
