"""ContextAssembler 测试。"""

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
