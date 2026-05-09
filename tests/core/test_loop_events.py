"""AgentCore 事件流测试。"""

import asyncio
import logging
from pathlib import Path

from agent.core.loop import AgentCore
from agent.core.provider import LLMResponse, LLMStreamChunk
from agent.fitness.change_store import FitnessPendingChangeStore
from agent.gateway.normalizer import NormalizedMessage
from agent.scheduler.cron import CronScheduler
from agent.tools.runtime_context import RuntimeServices
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


class ShellApprovalProviderManager:
    """返回一个需要审批的 shell 工具调用。"""

    async def call(self, request):
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "shell done", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "准备执行命令",
                "tool_calls": [
                    {
                        "id": "tool-shell-1",
                        "name": "shell_exec",
                        "input": {"command": "echo ok", "timeout": 30},
                    }
                ],
            },
        )()


class SlowProviderManager:
    """用于验证 run interrupt 的慢响应 Provider。"""

    async def call(self, request):
        await asyncio.sleep(0.05)
        return type("Resp", (), {"type": "text", "text": "slow done", "tool_calls": None})()


class StreamingProviderManager:
    """用于验证核心循环会消费 provider 的流式输出。"""

    async def call(self, request):
        raise AssertionError("streaming path should be preferred")

    async def call_stream(self, request):
        yield LLMStreamChunk(type="text_delta", delta="你")
        yield LLMStreamChunk(type="text_delta", delta="好")
        yield LLMStreamChunk(
            type="response",
            response=LLMResponse(type="text", text="你好"),
        )


class CronListProviderManager:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, request):
        self.calls += 1
        first_content = request.messages[0]["content"] if request.messages else ""
        if "你是工具可见性路由器" in first_content:
            return type("Resp", (), {"type": "text", "text": "scheduling", "tool_calls": None})()
        if self.calls > 2:
            raise AssertionError("cron_list_tasks should not require a second main-model call")
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "",
                "tool_calls": [{"id": "tool-cron-list", "name": "cron_list_tasks", "input": {}}],
            },
        )()


class ReminderPastProviderManager:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, request):
        self.calls += 1
        first_content = request.messages[0]["content"] if request.messages else ""
        if "你是工具可见性路由器" in first_content:
            return type("Resp", (), {"type": "text", "text": "scheduling", "tool_calls": None})()
        if self.calls > 2:
            raise AssertionError("failed reminder_create should return a direct error response")
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "",
                "tool_calls": [
                    {
                        "id": "tool-reminder-past",
                        "name": "reminder_create",
                        "input": {
                            "title": "喝水提醒",
                            "message": "该喝水了！",
                            "run_at": "2026-04-27T13:10:00+08:00",
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ],
            },
        )()


class FitnessProfileUpdateProviderManager:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, request):
        self.calls += 1
        if any(message["role"] == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "done", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "",
                "tool_calls": [
                    {
                        "id": "tool-fitness-1",
                        "name": "fitness_profile_update",
                        "input": {"goal": "力量提升", "plan_style": "PPL"},
                    }
                ],
            },
        )()


def test_run_events_emits_tool_trace(tmp_path: Path) -> None:
    """run_events 应暴露工具调用轨迹，而不仅是最终文本。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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


def test_cron_list_tool_result_returns_direct_response_without_second_model_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    provider = CronListProviderManager()
    scheduler = CronScheduler(
        config_path=workspace / "CRON.yaml",
        workspace_dir=workspace,
        agent_core=None,
        gateway=None,
    )
    scheduler.create_or_update_task(
        name="daily",
        schedule="0 8 * * *",
        timezone="UTC",
        action={"type": "prompt", "prompt": "早报"},
        output={},
    )
    core = AgentCore.build_for_test(
        workspace,
        provider,
        runtime_services=RuntimeServices(cron_scheduler=scheduler),
    )
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-cron",
        sender="user",
        body="调用 cron_list_tasks 看看",
        attachments=[],
        channel="feishu",
        metadata={},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    assert provider.calls == 2
    assert events[-1].kind == "run_finished"
    assert events[-1].result_text.startswith("你现在有 1 个定时任务")


def test_fitness_profile_update_tool_stages_major_change_in_runtime_loop(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    provider = FitnessProfileUpdateProviderManager()
    runtime_services = RuntimeServices(fitness_change_store=FitnessPendingChangeStore())
    core = AgentCore.build_for_test(
        workspace,
        provider,
        runtime_services=runtime_services,
    )

    message = NormalizedMessage(
        message_id="msg-fitness-stage",
        session_id="chat-stage",
        sender="ou-user-1",
        body="把我的训练目标改成力量提升，分化改成 PPL",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    tool_result = next(event for event in events if event.kind == "tool_call_result")
    assert "需要确认" in tool_result.content
    assert "健身档案" in tool_result.content
    profile_text = (workspace / "fitness" / "PROFILE.json").read_text(encoding="utf-8")
    assert '"goal": ""' in profile_text
    pending = runtime_services.fitness_change_store.get(
        "feishu:feishu:chat-stage",
        sender="ou-user-1",
    )
    assert pending is not None
    assert pending.target == "profile"


def test_memory_items_do_not_include_saved_notes_by_default(tmp_path: Path) -> None:
    from agent.memory import NoteStore

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    note_store = NoteStore(workspace / "agent.db")
    note_store.add_note(
        note_type="assistant_profile",
        title="助手称呼",
        content="用户希望助手被称呼为曾国藩。",
        importance="high",
        is_user_explicit=True,
        source_message_id="msg-1",
        source_thread_id="thread-1",
    )
    core = AgentCore.build_for_test(workspace, FakeProviderManager())
    core.note_store = note_store

    memory_items = asyncio.run(core._build_memory_items_text(
        user_message="你是谁",
        sender_id="ou-user-1",
        thread_id="thread-1",
    ))

    assert memory_items == ""


def test_failed_reminder_create_returns_direct_error_without_retry(tmp_path: Path) -> None:
    from datetime import datetime

    from agent.scheduler.reminder import ReminderScheduler

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    provider = ReminderPastProviderManager()
    scheduler = ReminderScheduler(
        config_path=workspace / "REMINDERS.yaml",
        workspace_dir=workspace,
        agent_core=None,
        gateway=None,
        now_provider=lambda: datetime.fromisoformat("2026-04-27T14:30:00+08:00"),
    )
    core = AgentCore.build_for_test(
        workspace,
        provider,
        runtime_services=RuntimeServices(reminder_scheduler=scheduler),
    )
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-reminder",
        sender="user",
        body="设置一个5分钟后的提醒，让我喝水",
        attachments=[],
        channel="feishu",
        metadata={},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    assert provider.calls == 2
    assert events[-1].kind == "run_finished"
    assert events[-1].result_text.startswith("创建提醒失败")
    assert scheduler.list_reminders() == []


def test_run_events_emits_interrupt_for_approval_required_tool(tmp_path: Path) -> None:
    """命中审批工具时，应挂起 run 并发出 interrupt 自定义事件。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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


def test_run_events_shell_exec_does_not_require_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(
        workspace,
        ShellApprovalProviderManager(),
        enable_shell=True,
        shell_requires_confirmation=True,
    )
    approvals = PendingApprovalStore()
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-shell",
        sender="user",
        body="执行 echo ok",
        attachments=[],
        channel="web",
        metadata={"run_id": "run-shell"},
    )

    async def collect():
        return [event async for event in core.run_events(message, approval_store=approvals)]

    events = asyncio.run(collect())

    # shell_exec does not require approval
    assert not any(event.kind == "custom" and event.name == "on_interrupt" for event in events)
    assert events[-1].kind == "run_finished"


def test_run_events_can_resume_after_approval(tmp_path: Path) -> None:
    """审批通过后，应能从待审批工具继续执行并最终完成。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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


def test_run_events_streams_multiple_text_deltas_from_provider(tmp_path: Path) -> None:
    """provider 支持流式时，核心循环应逐段透传文本 delta。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, StreamingProviderManager())
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-stream",
        sender="user",
        body="你好",
        attachments=[],
        channel="web",
        metadata={},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    deltas = [event.delta for event in events if event.kind == "assistant_text_delta"]
    assert deltas == ["你", "好"]
    assert events[-1].kind == "run_finished"
    assert events[-1].result_text == "你好"


def test_run_events_logs_first_token_for_streaming_provider(tmp_path: Path, caplog) -> None:
    """provider 流式输出时，应记录首 token 时间点。"""

    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, StreamingProviderManager())
    message = NormalizedMessage(
        message_id="stream-log-1",
        session_id="thread-stream-log",
        sender="user",
        body="你好",
        attachments=[],
        channel="web",
        metadata={},
    )
    caplog.set_level(logging.INFO, logger="agent.core.loop")

    async def collect():
        return [event async for event in core.run_events(message)]

    asyncio.run(collect())

    log_text = caplog.text
    assert "event=model_first_token" in log_text
    assert "event=model_response_completed" in log_text

