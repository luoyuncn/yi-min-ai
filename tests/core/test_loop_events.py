"""AgentCore 事件流测试。"""

import asyncio
from pathlib import Path

from agent.core.loop import AgentCore
from agent.gateway.normalizer import NormalizedMessage
from agent.web.runtime_state import PendingApprovalStore, RunControl


class FakeProviderManager:
    """先请求工具，再返回最终文本。"""

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "done", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "",
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "notes.txt"}}],
            },
        )()


class ApprovalProviderManager:
    """返回一个需要审批的文件写入工具调用。"""

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "已处理审批结果", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "准备写文件",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "name": "file_write",
                        "input": {"path": "notes.txt", "content": "hello from approval"},
                    }
                ],
            },
        )()


class SlowProviderManager:
    """用于验证 run interrupt 的慢响应 Provider。"""

    async def call(self, request):
        await asyncio.sleep(0.05)
        return type("Resp", (), {"type": "text", "text": "slow done", "tool_calls": None})()


def test_run_events_emits_tool_trace(tmp_path: Path) -> None:
    """run_events 应暴露工具调用轨迹，而不仅是最终文本。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, FakeProviderManager())
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-1",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="web",
        metadata={},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    assert any(event.kind == "tool_call_started" for event in events)
    assert any(event.kind == "tool_call_result" for event in events)
    assert events[-1].kind == "run_finished"


def test_run_events_emits_interrupt_for_approval_required_tool(tmp_path: Path) -> None:
    """命中审批工具时，应挂起 run 并发出 interrupt 自定义事件。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, ApprovalProviderManager())
    approvals = PendingApprovalStore()
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-approval",
        sender="user",
        body="请写入 notes.txt",
        attachments=[],
        channel="web",
        metadata={"run_id": "run-1"},
    )

    async def collect():
        return [event async for event in core.run_events(message, approval_store=approvals)]

    events = asyncio.run(collect())

    assert any(event.kind == "custom" and event.name == "on_interrupt" for event in events)
    assert not any(event.kind == "tool_call_started" for event in events)
    assert not any(event.kind == "tool_call_args" for event in events)
    assert events[-1].kind == "run_finished"
    assert approvals.get_by_thread("thread-approval") is not None


def test_run_events_can_resume_after_approval(tmp_path: Path) -> None:
    """审批通过后，应能从待审批工具继续执行并最终完成。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, ApprovalProviderManager())
    approvals = PendingApprovalStore()

    start_message = NormalizedMessage(
        message_id="1",
        session_id="thread-approval",
        sender="user",
        body="请写入 notes.txt",
        attachments=[],
        channel="web",
        metadata={"run_id": "run-1"},
    )

    async def collect(message):
        return [event async for event in core.run_events(message, approval_store=approvals)]

    asyncio.run(collect(start_message))
    pending = approvals.get_by_thread("thread-approval")

    assert pending is not None

    resume_message = NormalizedMessage(
        message_id="2",
        session_id="thread-approval",
        sender="user",
        body="",
        attachments=[],
        channel="web",
        metadata={
            "run_id": "run-2",
            "command": {
                "resume": {"approved": True},
                "interrupt_event": {"approval_id": pending.approval_id},
            },
        },
    )

    events = asyncio.run(collect(resume_message))

    assert any(event.kind == "step_started" and event.step_name == "approval-resume" for event in events)
    assert any(event.kind == "step_finished" and event.step_name == "approval-resume" for event in events)
    assert any(event.kind == "tool_call_started" for event in events)
    assert any(event.kind == "tool_call_args" for event in events)
    assert any(event.kind == "tool_call_result" for event in events)
    assert events[-1].kind == "run_finished"
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "hello from approval"


def test_run_events_stops_when_runtime_control_is_interrupted(tmp_path: Path) -> None:
    """收到 interrupt 请求后，运行中的 run 应在安全边界结束。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nAtlas\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, SlowProviderManager())
    control = RunControl(thread_id="thread-stop", run_id="run-stop")
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-stop",
        sender="user",
        body="慢一点回复",
        attachments=[],
        channel="web",
        metadata={"run_id": "run-stop"},
    )

    async def collect():
        async def trigger_interrupt():
            await asyncio.sleep(0.01)
            control.interrupt()

        interrupter = asyncio.create_task(trigger_interrupt())
        try:
            return [event async for event in core.run_events(message, runtime_control=control)]
        finally:
            await interrupter

    events = asyncio.run(collect())

    assert any(event.kind == "run_error" and event.code == "RunInterrupted" for event in events)
