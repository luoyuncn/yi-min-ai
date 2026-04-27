"""ContextAssembler 测试。"""

from datetime import datetime

from agent.core.context import ContextAssembler


def test_context_assembler_includes_system_memory_skills_history_and_user_message() -> None:
    """上下文中应同时包含系统层、历史消息和本轮用户输入。"""

    assembler = ContextAssembler(system_prompt="You are Yi Min.")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="Available Tools:\n- file_read: Read a text file",
        skill_index="Available Skills:\n- daily-briefing: Generate daily briefing",
        history=[{"role": "assistant", "content": "你好"}],
        user_message="帮我总结今天做了什么",
    )

    assert context[0]["role"] == "system"
    assert "prefers python" in context[0]["content"]
    assert "Available Tools:" in context[0]["content"]
    assert context[-1]["role"] == "user"


def test_context_assembler_includes_dynamic_system_time() -> None:
    """系统提示词中应注入当前系统时间，避免模型误判日期。"""

    assembler = ContextAssembler(
        system_prompt="You are Yi Min.",
        now_provider=lambda: datetime.fromisoformat("2026-04-23T18:40:00+08:00"),
    )

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="Available Tools:\n- ledger_upsert_draft: Save one ledger draft",
        skill_index="Available Skills:\n- bookkeeping: record ledger entries",
        history=[],
        user_message="帮我记一笔午饭支出 18 元",
    )

    system_content = context[0]["content"]
    assert "[SYSTEM TIME]" in system_content
    assert "2026-04-23" in system_content
    assert "18:40:00" in system_content


def test_context_assembler_includes_tool_and_skill_index_blocks() -> None:
    """系统上下文应同时显式暴露工具索引和技能索引。"""

    assembler = ContextAssembler(system_prompt="You are Yi Min.")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="Available Tools:\n- note_add: Create one note",
        skill_index="Available Skills:\n- note-taking: save durable facts",
        history=[],
        user_message="你有哪些 tools 和 skills",
    )

    system_content = context[0]["content"]

    assert "[TOOL INDEX]" in system_content
    assert "note_add: Create one note" in system_content
    assert "[SKILL INDEX]" in system_content
    assert "note-taking: save durable facts" in system_content


def test_context_assembler_includes_feishu_rendering_hint() -> None:
    """飞书渠道应显式提示避免使用 Markdown 表格。"""

    assembler = ContextAssembler(system_prompt="You are Yi Min.")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="Available Tools:\n- ledger_summary: Summarize entries",
        skill_index="Available Skills:\n- bookkeeping: record ledger entries",
        history=[],
        user_message="帮我总结今天餐饮支出",
        channel="feishu",
        channel_instance="feishu-main",
    )

    system_content = context[0]["content"]

    assert "[CHANNEL CONTEXT]" in system_content
    assert "Current channel: feishu/feishu-main" in system_content
    assert "Avoid Markdown tables" in system_content


def test_context_assembler_includes_human_context_and_memory_items() -> None:
    """当前说话人和检索到的记忆应进入独立上下文块。"""

    assembler = ContextAssembler(system_prompt="You are Yi Min.")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n",
        tool_index="Available Tools:",
        skill_index="Available Skills:",
        history=[],
        user_message="我喜欢喝什么？",
        channel="feishu",
        channel_instance="feishu",
        sender="ou-user-1",
        metadata={"chat_type": "group"},
        memory_items_text="- preference: 腿哥喜欢 Tims 冷萃美式。",
    )

    system_content = context[0]["content"]

    assert "[HUMAN CONTEXT]" in system_content
    assert "Current sender: ou-user-1" in system_content
    assert "Chat type: group" in system_content
    assert "[MEMORY ITEMS]" in system_content
    assert "Tims 冷萃美式" in system_content

