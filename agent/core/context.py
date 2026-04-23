"""上下文组装器。

这里的职责只有一个：
把系统提示、Always-On Memory、Skill 索引、会话历史和当前用户输入，
整理成一次模型调用所需的 `messages` 列表。
"""

class ContextAssembler:
    """负责拼装一次调用的模型上下文。"""

    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    def assemble(
        self,
        soul_text: str,
        memory_text: str,
        skill_index: str,
        history: list[dict],
        user_message: str,
    ) -> list[dict]:
        """把所有上下文片段按固定顺序组装起来。"""

        # 系统层内容被收敛成一条大的 system message，
        # 这样模型每次调用都能稳定拿到人格、长期记忆和技能索引。
        system_content = "\n\n".join(
            [
                self.system_prompt,
                "[SOUL.md]",
                soul_text,
                "[MEMORY.md]",
                memory_text,
                "[SKILL INDEX]",
                skill_index,
            ]
        )
        return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": user_message}]
