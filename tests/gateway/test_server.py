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
)


class FakeFeishuAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def reply_markdown(self, source_message_id: str, markdown: str, *, status: str | None = None) -> str:
        self.calls.append(("reply_markdown", source_message_id, f"{status or ''}|{markdown}"))
        return "bot-msg-1"

    async def update_markdown(self, message_id: str, markdown: str, *, status: str | None = None) -> None:
        self.calls.append(("update_markdown", message_id, f"{status or ''}|{markdown}"))


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
    """飞书通道应先回复收到，再把最终答案更新到同一条卡片消息。"""

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
    assert adapter.calls[0] == ("reply_markdown", "src-msg-1", "👀 已收到，正在思考…|")
    assert adapter.calls[1] == (
        "update_markdown",
        "bot-msg-1",
        "|**你好**\n\n- 能力 A",
    )


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
    assert adapter.calls[0] == ("reply_markdown", "src-msg-2", "👀 已收到，正在思考…|")
    assert adapter.calls[1] == ("update_markdown", "bot-msg-1", "✍️ 正在输出…|第一段")
    assert adapter.calls[2] == ("update_markdown", "bot-msg-1", "✍️ 正在输出…|第一段\n第二段")
    assert adapter.calls[3] == ("update_markdown", "bot-msg-1", "|第一段\n第二段")


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
    assert ops_adapter.calls[0] == ("reply_markdown", "src-ops-1", "👀 已收到，正在思考…|")
    assert ops_adapter.calls[1] == ("update_markdown", "bot-msg-1", "|来自 运维")
