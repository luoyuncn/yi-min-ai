"""AgentCore 核心循环测试。

这里重点保护：
1. 工具调用正常闭环
2. 工具失败也不会把整个 Agent 直接打崩
"""

from pathlib import Path

from agent.core.loop import AgentCore
from agent.gateway.normalizer import NormalizedMessage


class FakeProviderManager:
    """测试用假 Provider：先请求读文件，再给出最终文本回复。"""

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "已读取文件", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": None,
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "notes.txt"}}],
            },
        )()


def test_agent_core_can_execute_tool_then_finish(tmp_path: Path) -> None:
    """标准工具调用路径：模型请求工具 -> 工具执行 -> 模型收口。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    message = NormalizedMessage(
        message_id="1",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )
    core = AgentCore.build_for_test(workspace, FakeProviderManager())

    result = core.run_sync(message)

    assert result == "已读取文件"


class ErroringToolProviderManager:
    """测试用假 Provider：故意请求一个会失败的工具调用。"""

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "工具错误已处理", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": None,
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "missing.txt"}}],
            },
        )()


def test_agent_core_turns_tool_errors_into_recoverable_tool_messages(tmp_path: Path) -> None:
    """工具异常应该变成可恢复的工具结果，而不是直接抛出。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")

    message = NormalizedMessage(
        message_id="1",
        session_id="cli:default",
        sender="user",
        body="读取 missing.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )
    core = AgentCore.build_for_test(workspace, ErroringToolProviderManager())

    result = core.run_sync(message)

    assert result == "工具错误已处理"
