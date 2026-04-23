"""Skill 模块公开入口。

一期的 Skill 只做两件事：
1. 生成可注入上下文的技能索引
2. 在模型需要时读取某个技能全文
"""

from agent.skills.loader import SkillLoader

__all__ = ["SkillLoader"]
