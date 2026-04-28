"""AgentCore 核心循环测试。

这里重点保护：
1. 工具调用正常闭环
2. 工具失败也不会把整个 Agent 直接打崩
"""

import logging
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent.core.loop import AgentCore
from agent.gateway.normalizer import NormalizedMessage
from agent.memory.memory_store import MemoryStore


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
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
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


def test_agent_core_logs_timeline_for_model_and_tool_execution(tmp_path: Path, caplog) -> None:
    """核心循环应记录模型调用、工具执行和总耗时日志。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")

    message = NormalizedMessage(
        message_id="trace-msg-1",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )
    core = AgentCore.build_for_test(workspace, FakeProviderManager())
    caplog.set_level(logging.INFO, logger="agent.core.loop")

    result = core.run_sync(message)

    assert result == "已读取文件"
    log_text = caplog.text
    assert "event=run_started" in log_text
    assert "trace_id=" in log_text
    assert "event=context_assembled" in log_text
    assert "event=model_request_started" in log_text
    assert "event=model_response_completed" in log_text
    assert "event=tool_execution_started" in log_text
    assert "event=tool_execution_completed" in log_text
    assert "event=run_timing_summary" in log_text
    assert "event=run_finished" in log_text


def test_agent_core_ingest_to_mflow_uses_session_history_without_warning(tmp_path: Path, caplog) -> None:
    """M-flow 写入不应再依赖不存在的 get_history。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, FakeProviderManager())

    ingested: list[object] = []

    async def ingest_turn(turn_data):
        ingested.append(turn_data)

    core.mflow_bridge = SimpleNamespace(ingest_turn=ingest_turn)
    caplog.set_level(logging.WARNING, logger="agent.core.loop")

    message = NormalizedMessage(
        message_id="mflow-msg-1",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )

    result = core.run_sync(message)

    assert result == "已读取文件"
    assert len(ingested) == 1
    assert "M-flow ingestion failed" not in caplog.text


class CapturingProviderManager:
    def __init__(self) -> None:
        self.requests = []

    async def call(self, request):
        self.requests.append(request)
        return type("Resp", (), {"type": "text", "text": "好的，已记住。", "tool_calls": None})()


class BlockingMemoryExtractor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract_async(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return []


class LedgerQueryProviderManager:
    def __init__(self) -> None:
        self.requests = []

    async def call(self, request):
        self.requests.append(request)
        if any(message.get("role") == "tool" for message in request.messages):
            return type("Resp", (), {"type": "text", "text": "Tims 已在今天账本里。", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": None,
                "tool_calls": [
                    {
                        "id": "tool-ledger-query",
                        "name": "ledger_query_entries",
                        "input": {
                            "limit": 10,
                            "occurred_from": "2026-04-27T00:00:00+08:00",
                            "occurred_to": "2026-04-28T00:00:00+08:00",
                        },
                    }
                ],
            },
        )()


def test_agent_core_can_query_ledger_entries_for_follow_up_item(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    provider = LedgerQueryProviderManager()
    core = AgentCore.build_for_test(workspace, provider)
    core.ledger_store.add_entry(
        direction="expense",
        amount_cent=1500,
        currency="CNY",
        category="beverage",
        occurred_at="2026-04-27T08:00:00+08:00",
        merchant="Tims",
        note=None,
        source_message_id="old",
        source_thread_id="chat-ledger",
    )

    message = NormalizedMessage(
        message_id="latest",
        session_id="chat-ledger",
        sender="ou-user-1",
        body="我喝的tims呢",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    result = core.run_sync(message)

    assert result == "Tims 已在今天账本里。"
    assert len(provider.requests) == 2
    tool_messages = [message for message in provider.requests[-1].messages if message.get("role") == "tool"]
    assert tool_messages
    assert "Tims" in tool_messages[0]["content"]


class RecordingTraceObservation:
    def __init__(self, recorder, kind: str, name: str, **fields) -> None:
        self.recorder = recorder
        self.kind = kind
        self.name = name
        self.fields = fields

    def __enter__(self):
        self.recorder.events.append({"event": "start", "kind": self.kind, "name": self.name, **self.fields})
        return self

    def __exit__(self, exc_type, exc, tb):
        self.recorder.events.append({"event": "end", "kind": self.kind, "name": self.name, "error": str(exc) if exc else None})
        return False

    def update(self, **fields):
        self.recorder.events.append({"event": "update", "kind": self.kind, "name": self.name, **fields})


class RecordingTraceClient:
    def __init__(self) -> None:
        self.events = []

    def start_trace(self, name: str, **fields):
        return RecordingTraceObservation(self, "trace", name, **fields)

    def start_span(self, name: str, **fields):
        return RecordingTraceObservation(self, "span", name, **fields)

    def start_generation(self, name: str, **fields):
        return RecordingTraceObservation(self, "generation", name, **fields)

    def start_tool(self, name: str, **fields):
        return RecordingTraceObservation(self, "tool", name, **fields)

    def flush(self):
        self.events.append({"event": "flush"})


def test_agent_core_records_langfuse_style_trace_for_model_and_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    tracer = RecordingTraceClient()
    core = AgentCore.build_for_test(workspace, FakeProviderManager(), trace_client=tracer)

    message = NormalizedMessage(
        message_id="trace-langfuse-msg",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )

    core.run_sync(message)

    assert any(event["event"] == "start" and event["kind"] == "trace" and event["name"] == "agent.run" for event in tracer.events)
    assert any(event["event"] == "start" and event["kind"] == "generation" and event["name"] == "llm.chat" for event in tracer.events)
    assert any(event["event"] == "start" and event["kind"] == "tool" and event["name"] == "tool.file_read" for event in tracer.events)
    assert any(event["event"] == "update" and event["kind"] == "tool" and "hello" in event.get("output", "") for event in tracer.events)
    assert not any(event["event"] == "flush" for event in tracer.events)


def test_agent_core_extracts_and_injects_memory_items(tmp_path: Path) -> None:
    """成功回复后应写入记忆，下一轮应自动注入相关记忆。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    memory_store = MemoryStore(workspace / "agent.db")
    provider = CapturingProviderManager()
    core = AgentCore.build_for_test(workspace, provider, memory_store=memory_store)

    first = NormalizedMessage(
        message_id="msg-remember",
        session_id="chat-1",
        sender="ou-user-1",
        body="记住我喜欢 Tims 冷萃美式",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )
    second = NormalizedMessage(
        message_id="msg-recall",
        session_id="chat-1",
        sender="ou-user-1",
        body="我喜欢喝什么？",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    core.run_sync(first)
    core.run_sync(second)

    assert memory_store.search("冷萃", limit=5)
    second_system_content = provider.requests[-1].messages[0]["content"]
    assert "[检索到的长期记忆]" in second_system_content
    assert "Tims 冷萃美式" in second_system_content
    assert "[用户上下文]" in second_system_content
    assert "ou-user-1" in second_system_content


def test_agent_core_runs_memory_extraction_in_background(tmp_path: Path) -> None:
    """记忆抽取不应阻塞本轮 RunFinishedEvent。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    memory_store = MemoryStore(workspace / "agent.db")
    provider = CapturingProviderManager()
    core = AgentCore.build_for_test(workspace, provider, memory_store=memory_store)

    message = NormalizedMessage(
        message_id="msg-background-memory",
        session_id="chat-background-memory",
        sender="ou-user-1",
        body="记住我喜欢 Tims 冷萃美式",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    async def run_and_release_memory_task():
        extractor = BlockingMemoryExtractor()
        core.memory_extractor = extractor

        async def wait_for_finished():
            async for event in core.run_events(message):
                if event.kind == "run_finished":
                    return event
            raise AssertionError("run_finished was not emitted")

        finished = await asyncio.wait_for(wait_for_finished(), timeout=0.2)
        await asyncio.wait_for(extractor.started.wait(), timeout=0.2)
        extractor.release.set()
        await asyncio.wait_for(core.drain_background_tasks(), timeout=0.2)
        return finished

    finished_event = asyncio.run(run_and_release_memory_task())

    assert finished_event.result_text == "好的，已记住。"


def test_agent_core_limits_long_history_in_model_context(tmp_path: Path) -> None:
    """长会话应保留最近 10 个用户轮次，即约 20 条历史消息。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    provider = CapturingProviderManager()
    core = AgentCore.build_for_test(workspace, provider)

    async def seed_session():
        session = await core.session_manager.get_or_create("feishu:feishu:chat-long", channel="feishu")
        for index in range(30):
            session.append(
                {
                    "id": f"user-{index}",
                    "role": "user",
                    "content": f"old user message {index}",
                }
            )
            session.append(
                {
                    "id": f"assistant-{index}",
                    "role": "assistant",
                    "content": f"old assistant message {index}",
                }
            )

    import asyncio

    asyncio.run(seed_session())

    message = NormalizedMessage(
        message_id="latest",
        session_id="chat-long",
        sender="ou-user-1",
        body="今日有何新鲜事",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    core.run_sync(message)

    sent_messages = provider.requests[-1].messages
    sent_text = "\n".join(message.get("content", "") for message in sent_messages)
    sent_user_messages = [message for message in sent_messages if message.get("role") == "user"]
    assert "old user message 19" not in sent_text
    assert "old assistant message 19" not in sent_text
    assert "old user message 20" in sent_text
    assert "old assistant message 29" in sent_text
    assert len(sent_user_messages) == 11


def test_agent_core_removes_historical_tool_payloads_from_model_context(tmp_path: Path) -> None:
    """历史工具参数不应把旧 SOUL 内容重新注入新一轮上下文。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\n你是银月。\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    provider = CapturingProviderManager()
    core = AgentCore.build_for_test(workspace, provider)

    async def seed_session():
        session = await core.session_manager.get_or_create("feishu:feishu:chat-stale-tools", channel="feishu")
        session.append(
            {
                "id": "old-user",
                "role": "user",
                "content": "你叫曾国藩，根据他的原型完善你的SOUL",
            }
        )
        session.append(
            {
                "id": "old-assistant-tool",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "old-tool",
                        "name": "file_write",
                        "input": {"path": "SOUL.md", "content": "你是曾国藩。"},
                    }
                ],
            }
        )
        session.append({"id": "old-tool-result", "role": "tool", "tool_call_id": "old-tool", "content": "ok"})

    import asyncio

    asyncio.run(seed_session())

    message = NormalizedMessage(
        message_id="latest",
        session_id="chat-stale-tools",
        sender="ou-user-1",
        body="你是谁",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    core.run_sync(message)

    sent_messages = provider.requests[-1].messages
    sent_text = "\n".join(message.get("content", "") for message in sent_messages)
    assert "你是银月" in sent_text
    assert "你叫曾国藩" not in sent_text
    assert "你是曾国藩" not in sent_text
    assert all("tool_calls" not in message for message in sent_messages[1:-1])
    assert not any(message.get("role") == "tool" for message in sent_messages[1:-1])


def test_agent_core_removes_historical_identity_persona_turns_from_model_context(tmp_path: Path) -> None:
    """普通问题也不应携带会覆盖当前 SOUL 的旧身份/人格对话。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\n你是银月。\n", encoding="utf-8")
    (workspace / "PROFILE.md").write_text("# User Profile\n", encoding="utf-8")
    provider = CapturingProviderManager()
    core = AgentCore.build_for_test(workspace, provider)

    async def seed_session():
        session = await core.session_manager.get_or_create("feishu:feishu:chat-persona-history", channel="feishu")
        for index in range(10):
            session.append({"id": f"user-{index}", "role": "user", "content": f"普通事实 {index}"})
            session.append({"id": f"assistant-{index}", "role": "assistant", "content": f"普通回复 {index}"})
        session.append({"id": "old-user-name", "role": "user", "content": "你叫曾国藩"})
        session.append({"id": "old-assistant-name", "role": "assistant", "content": "好的，曾国藩。"})
        session.append({"id": "old-user-soul", "role": "user", "content": "你叫曾国藩，根据他的原型完善你的SOUL"})
        session.append({"id": "old-assistant-soul", "role": "assistant", "content": "大人，鄙人曾国藩，字伯涵，号涤生。"})
        session.append({"id": "normal-user-after-stale-persona", "role": "user", "content": "你有哪些工具"})
        session.append({"id": "stale-persona-normal-answer", "role": "assistant", "content": "国藩手中有几件工具。"})
        session.append({"id": "old-user-identity", "role": "user", "content": "你是谁"})
        session.append({"id": "old-assistant-identity", "role": "assistant", "content": "鄙人曾国藩。"})

    import asyncio

    asyncio.run(seed_session())

    message = NormalizedMessage(
        message_id="latest",
        session_id="chat-persona-history",
        sender="ou-user-1",
        body="你有哪些工具",
        attachments=[],
        channel="feishu",
        channel_instance="feishu",
        metadata={"chat_type": "p2p"},
    )

    core.run_sync(message)

    sent_messages = provider.requests[-1].messages
    sent_text = "\n".join(message.get("content", "") for message in sent_messages)
    assert "普通事实 9" in sent_text
    assert "你是银月" in sent_text
    assert "你叫曾国藩" not in sent_text
    assert "国藩手中" not in sent_text
    assert "鄙人曾国藩" not in sent_text
    assert "完善你的SOUL" not in sent_text


def test_agent_core_writes_react_log_for_model_decision_and_tool_result(tmp_path: Path) -> None:
    """ReAct 轨迹应单独写入 logs/react.log，包含模型决策和工具结果。"""

    workspace = tmp_path / "workspace"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("hello from file", encoding="utf-8")
    core = AgentCore.build_for_test(workspace, FakeProviderManager())

    message = NormalizedMessage(
        message_id="react-msg-1",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )

    result = core.run_sync(message)

    assert result == "已读取文件"
    react_log = workspace / "logs" / "react.log"
    lines = [json.loads(line) for line in react_log.read_text(encoding="utf-8").splitlines()]
    event_names = [line["event"] for line in lines]
    assert "model_response" in event_names
    assert "decision" in event_names
    assert "tool_call" in event_names
    assert "tool_result" in event_names
    tool_result = next(line for line in lines if line["event"] == "tool_result")
    assert tool_result["tool_name"] == "file_read"
    assert "hello from file" in tool_result["result"]

