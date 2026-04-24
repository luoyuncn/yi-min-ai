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
