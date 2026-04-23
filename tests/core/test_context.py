"""ContextAssembler 测试。"""

from datetime import datetime

from agent.core.context import ContextAssembler


def test_context_assembler_includes_system_memory_skills_history_and_user_message() -> None:
    """上下文中应同时包含系统层、历史消息和本轮用户输入。"""

    assembler = ContextAssembler(system_prompt="You are Atlas.")

    context = assembler.assemble(
        soul_text="# Identity\nAtlas",
        memory_text="# User Profile\n- prefers python",
        skill_index="Available Skills:\n- daily-briefing: Generate daily briefing",
        history=[{"role": "assistant", "content": "你好"}],
        user_message="帮我总结今天做了什么",
    )

    assert context[0]["role"] == "system"
    assert "prefers python" in context[0]["content"]
    assert context[-1]["role"] == "user"


def test_context_assembler_includes_dynamic_system_time() -> None:
    """系统提示词中应注入当前系统时间，避免模型误判日期。"""

    assembler = ContextAssembler(
        system_prompt="You are Atlas.",
        now_provider=lambda: datetime.fromisoformat("2026-04-23T18:40:00+08:00"),
    )

    context = assembler.assemble(
        soul_text="# Identity\nAtlas",
        memory_text="# User Profile\n- prefers python",
        skill_index="Available Skills:\n- bookkeeping: record ledger entries",
        history=[],
        user_message="帮我记一笔午饭支出 18 元",
    )

    system_content = context[0]["content"]
    assert "[SYSTEM TIME]" in system_content
    assert "2026-04-23" in system_content
    assert "18:40:00" in system_content
