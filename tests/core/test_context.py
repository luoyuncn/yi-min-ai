"""ContextAssembler 测试。"""

from datetime import datetime

from agent.core.context import ContextAssembler


def test_context_assembler_includes_system_memory_skills_history_and_user_message() -> None:
    """上下文中应同时包含系统层、历史消息和本轮用户输入。"""

    assembler = ContextAssembler(system_prompt="你是 Yi Min。")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="可用工具：\n- file_read: 读取文本文件",
        skill_index="可用技能：\n- daily-briefing: 生成每日简报",
        history=[{"role": "assistant", "content": "你好"}],
        user_message="帮我总结今天做了什么",
    )

    assert context[0]["role"] == "system"
    assert "[PROFILE.md]" in context[0]["content"]
    assert "[MEMORY.md]" not in context[0]["content"]
    assert "prefers python" in context[0]["content"]
    assert "可用工具" in context[0]["content"]
    assert context[-1]["role"] == "user"


def test_context_assembler_includes_dynamic_system_time() -> None:
    """系统提示词中应注入当前系统时间，避免模型误判日期。"""

    assembler = ContextAssembler(
        system_prompt="你是 Yi Min。",
        now_provider=lambda: datetime.fromisoformat("2026-04-23T18:40:00+08:00"),
    )

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="可用工具：\n- ledger_upsert_draft: 保存一条账本草稿",
        skill_index="可用技能：\n- bookkeeping: 记录账本条目",
        history=[],
        user_message="帮我记一笔午饭支出 18 元",
    )

    system_content = context[0]["content"]
    assert "[系统时间]" in system_content
    assert "2026-04-23" in system_content
    assert "18:40:00" in system_content
    assert "2026-04-23T18:40:00+08:00" in system_content


def test_context_assembler_marks_soul_as_identity_source_of_truth() -> None:
    assembler = ContextAssembler(system_prompt="你是 Yi Min。")

    context = assembler.assemble(
        soul_text="# Identity\n你是银月。",
        memory_text="# User Profile\n",
        tool_index="可用工具：",
        skill_index="可用技能：",
        history=[{"role": "assistant", "content": "我是曾国藩。"}],
        user_message="你是谁",
    )

    system_content = context[0]["content"]
    assert "[身份事实来源]" in system_content
    assert "SOUL.md` 是助手活跃身份" in system_content


def test_context_assembler_includes_tool_and_skill_index_blocks() -> None:
    """系统上下文应同时显式暴露工具索引和技能索引。"""

    assembler = ContextAssembler(system_prompt="你是 Yi Min。")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="可用工具：\n- note_add: 创建一条笔记",
        skill_index="可用技能：\n- note-taking: 保存长期事实",
        history=[],
        user_message="你有哪些 tools 和 skills",
    )

    system_content = context[0]["content"]

    assert "[工具索引]" in system_content
    assert "note_add: 创建一条笔记" in system_content
    assert "[技能索引]" in system_content
    assert "note-taking: 保存长期事实" in system_content


def test_context_assembler_includes_feishu_rendering_hint() -> None:
    """飞书渠道应显式提示避免使用 Markdown 表格。"""

    assembler = ContextAssembler(system_prompt="你是 Yi Min。")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        tool_index="可用工具：\n- ledger_summary: 汇总账目",
        skill_index="可用技能：\n- bookkeeping: 记录账本条目",
        history=[],
        user_message="帮我总结今天餐饮支出",
        channel="feishu",
        channel_instance="feishu-main",
    )

    system_content = context[0]["content"]

    assert "[渠道上下文]" in system_content
    assert "当前渠道：feishu/feishu-main" in system_content
    assert "避免使用 Markdown 表格" in system_content


def test_context_assembler_includes_human_context_and_memory_items() -> None:
    """当前说话人和检索到的记忆应进入独立上下文块。"""

    assembler = ContextAssembler(system_prompt="你是 Yi Min。")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n",
        tool_index="可用工具：",
        skill_index="可用技能：",
        history=[],
        user_message="我喜欢喝什么？",
        channel="feishu",
        channel_instance="feishu",
        sender="ou-user-1",
        metadata={"chat_type": "group"},
        memory_items_text="- preference: 腿哥喜欢 Tims 冷萃美式。",
    )

    system_content = context[0]["content"]

    assert "[用户上下文]" in system_content
    assert "当前发送者：ou-user-1" in system_content
    assert "聊天类型：group" in system_content
    assert "[检索到的长期记忆]" in system_content
    assert "Tims 冷萃美式" in system_content


def test_context_assembler_routes_one_shot_reminders_to_reminder_tools() -> None:
    assembler = ContextAssembler(
        system_prompt="你是 Yi Min。",
        now_provider=lambda: datetime.fromisoformat("2026-04-27T12:37:00+08:00"),
    )

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n",
        tool_index="可用工具：\n- reminder_create: 创建一次性提醒",
        skill_index="可用技能：",
        history=[],
        user_message="2分钟后提醒我起床",
        channel="feishu",
    )

    system_content = context[0]["content"]

    assert "一次性提醒、闹钟和相对时间提醒使用 `reminder_create`" in system_content
    assert "相对时间提醒应传入 `delay_seconds`" in system_content
    assert "用一句简短确认回复" in system_content
