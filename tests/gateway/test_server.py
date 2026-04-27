"""GatewayServer 的飞书通道行为测试。"""

import asyncio
import logging
from types import SimpleNamespace

from agent.gateway.normalizer import NormalizedMessage
from agent.gateway.server import GatewayServer
from agent.web.events import (
    AssistantTextDeltaEvent,
    AssistantTextEndEvent,
    AssistantTextStartEvent,
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    ToolCallArgsEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)


class FakeFeishuAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def reply_card(self, source_message_id: str, card: dict) -> str:
        self.calls.append(("reply_card", source_message_id, card))
        return "bot-msg-1"

    async def update_card(self, message_id: str, card: dict) -> None:
        self.calls.append(("update_card", message_id, card))


class FakeCore:
    async def run_events(self, message):
        yield RunStartedEvent(thread_id=message.session_id, run_id=message.message_id)
        yield AssistantTextStartEvent(message_id="assistant-1")
        yield AssistantTextDeltaEvent(message_id="assistant-1", delta="**你好**\n\n- 能力 A")
        yield AssistantTextEndEvent(message_id="assistant-1")
        yield RunFinishedEvent(
            thread_id=message.session_id,
            run_id=message.message_id,
            result_text="**你好**\n\n- 能力 A",
        )


def test_gateway_server_feishu_replies_with_ack_then_updates_markdown() -> None:
    """飞书通道应先回复结构化占位卡，再更新为最终结构化卡片。"""

    gateway = GatewayServer(SimpleNamespace(core=FakeCore()))
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-1",
        session_id="chat-1",
        sender="user-1",
        body="你有哪些技能",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-msg-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "**你好**\n\n- 能力 A"
    assert adapter.calls[0][0] == "reply_card"
    assert adapter.calls[0][1] == "src-msg-1"
    assert adapter.calls[0][2]["header"]["title"]["content"] == "Yi Min 正在处理"
    assert adapter.calls[1][0] == "update_card"
    assert adapter.calls[1][2]["header"]["title"]["content"] == "Yi Min 回复"


class FakeStreamingCore:
    async def run_events(self, message):
        yield RunStartedEvent(thread_id=message.session_id, run_id=message.message_id)
        yield AssistantTextStartEvent(message_id="assistant-1")
        yield AssistantTextDeltaEvent(message_id="assistant-1", delta="第一段")
        yield AssistantTextDeltaEvent(message_id="assistant-1", delta="\n第二段")
        yield RunFinishedEvent(
            thread_id=message.session_id,
            run_id=message.message_id,
            result_text="第一段\n第二段",
        )


class BurstStreamingCore:
    def __init__(self) -> None:
        self.after_first_patch_started = asyncio.Event()
        self.second_delta_emitted = False

    async def run_events(self, message):
        yield RunStartedEvent(thread_id=message.session_id, run_id=message.message_id)
        yield AssistantTextStartEvent(message_id="assistant-burst")
        yield AssistantTextDeltaEvent(message_id="assistant-burst", delta="第一段")
        await self.after_first_patch_started.wait()
        self.second_delta_emitted = True
        yield AssistantTextDeltaEvent(message_id="assistant-burst", delta="第二段")
        yield RunFinishedEvent(
            thread_id=message.session_id,
            run_id=message.message_id,
            result_text="第一段第二段",
        )


class BlockingPatchFeishuAdapter(FakeFeishuAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.patch_started = asyncio.Event()
        self.release_patch = asyncio.Event()

    async def update_card(self, message_id: str, card: dict) -> None:
        self.patch_started.set()
        await self.release_patch.wait()
        await super().update_card(message_id, card)


def test_gateway_server_feishu_updates_card_during_streaming_output() -> None:
    """飞书通道应在生成过程中更新占位卡片，而不是只在结束时更新。"""

    gateway = GatewayServer(SimpleNamespace(core=FakeStreamingCore()))
    gateway._feishu_patch_interval_secs = 0.0
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-2",
        session_id="chat-2",
        sender="user-2",
        body="讲讲能力",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-msg-2"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "第一段\n第二段"
    assert adapter.calls[0][0] == "reply_card"
    assert adapter.calls[1][0] == "update_card"
    assert adapter.calls[1][2]["header"]["title"]["content"] == "Yi Min 正在输出"
    assert adapter.calls[2][2]["header"]["title"]["content"] == "Yi Min 正在输出"
    assert adapter.calls[3][2]["header"]["title"]["content"] == "Yi Min 回复"


def test_gateway_server_feishu_streaming_does_not_await_intermediate_patch() -> None:
    """中间流式 patch 不应阻塞继续消费模型事件。"""

    core = BurstStreamingCore()
    gateway = GatewayServer(SimpleNamespace(core=core))
    gateway._feishu_patch_interval_secs = 0.0
    adapter = BlockingPatchFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-fire-and-forget",
        session_id="chat-fire-and-forget",
        sender="user-fire-and-forget",
        body="讲讲能力",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-fire-and-forget"},
    )

    async def run_case():
        task = asyncio.create_task(gateway._handle_message(message))
        await asyncio.wait_for(adapter.patch_started.wait(), timeout=1)
        core.after_first_patch_started.set()
        await asyncio.sleep(0)
        assert core.second_delta_emitted is True
        assert not task.done()
        adapter.release_patch.set()
        return await asyncio.wait_for(task, timeout=1)

    result = asyncio.run(run_case())

    assert result == "第一段第二段"


def test_gateway_server_logs_feishu_processing_timeline(caplog) -> None:
    """飞书处理链路应输出收到、回执、流式开始和完成日志。"""

    gateway = GatewayServer(SimpleNamespace(core=FakeStreamingCore()))
    gateway._feishu_patch_interval_secs = 0.0
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter
    caplog.set_level(logging.INFO, logger="agent.gateway.server")

    message = NormalizedMessage(
        message_id="run-log-1",
        session_id="chat-log-1",
        sender="user-3",
        body="讲讲能力",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-msg-log-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "第一段\n第二段"
    log_text = caplog.text
    assert "event=feishu_message_started" in log_text
    assert "event=feishu_ack_sent" in log_text
    assert "event=feishu_streaming_started" in log_text
    assert "event=feishu_message_completed" in log_text
    assert "trace_id=" in log_text


class FakeNamedCore:
    def __init__(self, label: str) -> None:
        self._label = label

    async def run_events(self, message):
        text = f"来自 {self._label}"
        yield RunStartedEvent(thread_id=message.session_id, run_id=message.message_id)
        yield AssistantTextStartEvent(message_id=f"assistant-{self._label}")
        yield AssistantTextDeltaEvent(message_id=f"assistant-{self._label}", delta=text)
        yield AssistantTextEndEvent(message_id=f"assistant-{self._label}")
        yield RunFinishedEvent(
            thread_id=message.session_id,
            run_id=message.message_id,
            result_text=text,
        )


def test_gateway_server_routes_messages_to_runtime_specific_app_and_adapter() -> None:
    """不同飞书实例应路由到各自的 app runtime 和 adapter。"""

    gateway = GatewayServer(SimpleNamespace(core=FakeNamedCore("默认")))
    gateway.register_runtime_app("feishu-ops", SimpleNamespace(core=FakeNamedCore("运维")))

    default_adapter = FakeFeishuAdapter()
    ops_adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = default_adapter
    gateway.adapters["feishu-ops"] = ops_adapter

    message = NormalizedMessage(
        message_id="run-ops-1",
        session_id="chat-ops-1",
        sender="user-ops",
        body="你好",
        attachments=[],
        channel="feishu",
        channel_instance="feishu-ops",
        metadata={"source_message_id": "src-ops-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "来自 运维"
    assert default_adapter.calls == []
    assert ops_adapter.calls[0][0] == "reply_card"
    assert ops_adapter.calls[0][1] == "src-ops-1"
    assert ops_adapter.calls[1][0] == "update_card"
    assert ops_adapter.calls[1][2]["elements"][0]["elements"][0]["content"].startswith("你：")


class FakeArchive:
    def __init__(self, reserve_results: list[bool]) -> None:
        self.reserve_results = list(reserve_results)
        self.reserve_calls: list[dict] = []
        self.status_calls: list[dict] = []

    def reserve_inbound_message(self, **kwargs):
        self.reserve_calls.append(kwargs)
        return self.reserve_results.pop(0)

    def mark_channel_message_status(self, **kwargs):
        self.status_calls.append(kwargs)


class FakeQueue:
    def __init__(self) -> None:
        self.messages: list[NormalizedMessage] = []

    async def enqueue(self, message: NormalizedMessage) -> None:
        self.messages.append(message)


class FakeReceivingAdapter:
    def __init__(self, messages: list[NormalizedMessage]) -> None:
        self._messages = list(messages)

    async def receive(self):
        for message in self._messages:
            yield message


def test_gateway_server_drops_duplicate_message_before_enqueue() -> None:
    """同一条飞书消息若已在归档中登记，不应再次进入执行队列。"""

    archive = FakeArchive([True, False])
    gateway = GatewayServer(SimpleNamespace(core=SimpleNamespace(session_archive=archive)))
    gateway.command_queue = FakeQueue()
    gateway._running = True

    duplicate_a = NormalizedMessage(
        message_id="om-msg-duplicate",
        session_id="chat-duplicate",
        sender="user-duplicate",
        body="我今天中午吃啥了",
        attachments=[],
        channel="feishu",
        channel_instance="feishu-main",
        metadata={"source_message_id": "om-msg-duplicate"},
    )
    duplicate_b = NormalizedMessage(
        message_id="om-msg-duplicate",
        session_id="chat-duplicate",
        sender="user-duplicate",
        body="我今天中午吃啥了",
        attachments=[],
        channel="feishu",
        channel_instance="feishu-main",
        metadata={"source_message_id": "om-msg-duplicate"},
    )
    adapter = FakeReceivingAdapter([duplicate_a, duplicate_b])

    asyncio.run(gateway._receive_loop("feishu-main", adapter))

    assert len(archive.reserve_calls) == 2
    assert [message.message_id for message in gateway.command_queue.messages] == ["om-msg-duplicate"]


class FakeLedgerCore:
    async def run_events(self, message):
        yield RunStartedEvent(thread_id=message.session_id, run_id=message.message_id)
        yield ToolCallStartEvent(tool_call_id="tool-1", tool_call_name="ledger_upsert_draft")
        yield ToolCallArgsEvent(
            tool_call_id="tool-1",
            delta='{"thread_id":"default","direction":"expense","amount_cent":2700,"currency":"CNY","category":"meal","occurred_at":"2026-04-24T12:30:00+08:00","merchant":"老乡鸡","note":"酸菜鱼、鸡腿、狮子头"}',
        )
        yield ToolCallResultEvent(
            message_id="tool-msg-1",
            tool_call_id="tool-1",
            content="ok",
        )
        yield AssistantTextStartEvent(message_id="assistant-ledger")
        yield AssistantTextDeltaEvent(message_id="assistant-ledger", delta="午餐草稿已创建。需要我提交吗？")
        yield AssistantTextEndEvent(message_id="assistant-ledger")
        yield RunFinishedEvent(
            thread_id=message.session_id,
            run_id=message.message_id,
            result_text="午餐草稿已创建。需要我提交吗？",
        )


def test_gateway_server_renders_ledger_scene_as_structured_card() -> None:
    """记账场景应输出专门的结构化卡片，而不是普通文本块。"""

    gateway = GatewayServer(SimpleNamespace(core=FakeLedgerCore()))
    gateway._feishu_patch_interval_secs = 0.0
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-ledger-1",
        session_id="chat-ledger-1",
        sender="user-ledger",
        body="我中午吃了老乡鸡，27块钱",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-ledger-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "午餐草稿已创建。需要我提交吗？"
    final_card = adapter.calls[-1][2]
    assert final_card["header"]["title"]["content"] == "记账确认"
    field_texts = [
        field["text"]["content"]
        for element in final_card["elements"]
        if element.get("tag") == "div"
        for field in element.get("fields", [])
    ]
    assert any("老乡鸡" in text and "¥27.00" in text for text in field_texts)


class FakeApprovalInterruptCore:
    async def run_events(self, message, *, approval_store=None):
        yield RunStartedEvent(thread_id=message.thread_key, run_id=message.message_id)
        yield CustomEvent(
            name="on_interrupt",
            value={
                "approval_id": "approval-1",
                "thread_id": message.thread_key,
                "run_id": message.message_id,
                "tool_name": "shell_exec",
                "tool_call_id": "tool-shell-1",
                "args": {"command": "echo ok", "timeout": 30},
                "message": "Approval required for shell_exec",
            },
        )
        yield RunFinishedEvent(thread_id=message.thread_key, run_id=message.message_id, result_text="")


def test_gateway_server_feishu_shell_interrupt_renders_confirmation_prompt() -> None:
    gateway = GatewayServer(SimpleNamespace(core=FakeApprovalInterruptCore()))
    gateway._feishu_patch_interval_secs = 0.0
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-shell-1",
        session_id="chat-shell-1",
        sender="user-shell",
        body="帮我执行 echo ok",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-shell-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert "确认 approval-1" in result
    assert "拒绝 approval-1" in result
    assert adapter.calls[-1][0] == "update_card"
    assert "echo ok" in str(adapter.calls[-1][2])


class FakeApprovalResumeCore:
    def __init__(self) -> None:
        self.messages = []
        self.approval_stores = []

    async def run_events(self, message, *, approval_store=None):
        self.messages.append(message)
        self.approval_stores.append(approval_store)
        yield RunStartedEvent(thread_id=message.thread_key, run_id=message.message_id)
        yield AssistantTextStartEvent(message_id="assistant-resume")
        yield AssistantTextDeltaEvent(message_id="assistant-resume", delta="已执行")
        yield AssistantTextEndEvent(message_id="assistant-resume")
        yield RunFinishedEvent(thread_id=message.thread_key, run_id=message.message_id, result_text="已执行")


def test_gateway_server_feishu_confirmation_resumes_pending_approval() -> None:
    core = FakeApprovalResumeCore()
    gateway = GatewayServer(SimpleNamespace(core=core))
    adapter = FakeFeishuAdapter()
    gateway.adapters["feishu"] = adapter

    message = NormalizedMessage(
        message_id="run-confirm-1",
        session_id="chat-confirm-1",
        sender="user-confirm",
        body="确认 approval-1",
        attachments=[],
        channel="feishu",
        metadata={"source_message_id": "src-confirm-1"},
    )

    result = asyncio.run(gateway._handle_message(message))

    assert result == "已执行"
    assert core.messages[0].metadata["command"]["resume"]["approved"] is True
    assert core.messages[0].metadata["command"]["interrupt_event"]["approval_id"] == "approval-1"
    assert core.approval_stores[0] is gateway.pending_approvals
