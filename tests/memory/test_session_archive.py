"""SessionArchive 测试。

重点验证三件事：
1. 归档能写入并检索
2. 重复写同一 turn 时不会把索引写坏
3. 归档后的消息历史能被完整恢复
"""

from datetime import UTC, datetime
from pathlib import Path

from agent.memory.session_archive import SessionArchive
from agent.session.models import Session, SessionMetadata


def test_session_archive_can_write_and_search_turns(tmp_path: Path) -> None:
    """基础全文检索路径。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("cli:default", 0, "user", "请记住我喜欢 Python")
    archive.append_turn("cli:default", 1, "assistant", "收到，我会记住")

    rows = archive.search("Python", limit=5)

    assert len(rows) == 1
    assert rows[0]["role"] == "user"


def test_session_archive_can_replace_existing_turn_without_breaking_search(tmp_path: Path) -> None:
    """同一 turn 被覆盖后，FTS 检索应指向最新内容。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("cli:default", 0, "user", "第一版")
    archive.append_turn("cli:default", 0, "user", "第二版")

    rows = archive.search("第二版", limit=5)

    assert len(rows) == 1
    assert rows[0]["content"] == "第二版"


def test_session_archive_can_restore_full_session_history(tmp_path: Path) -> None:
    """归档后的完整内部消息结构应可被恢复，用于线程重放。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    now = datetime.now(UTC)
    session = Session(
        metadata=SessionMetadata(
            session_id="web:thread-1",
            channel="web",
            created_at=now,
            last_active_at=now,
        ),
        history=[
            {"id": "user-1", "role": "user", "content": "帮我读取 notes.txt"},
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "notes.txt"}}],
            },
            {"id": "tool-msg-1", "role": "tool", "tool_call_id": "tool-1", "content": "hello"},
        ],
    )

    archive.persist_session(session)
    restored = archive.load_session("web:thread-1")

    assert restored is not None
    assert [message["id"] for message in restored.history] == ["user-1", "assistant-1", "tool-msg-1"]
    assert restored.history[1]["tool_calls"][0]["name"] == "file_read"
    assert restored.history[2]["tool_call_id"] == "tool-1"


def test_session_archive_lists_recent_threads_for_thread_picker(tmp_path: Path) -> None:
    """线程列表接口需要能从归档里拿到最近活跃线程摘要。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("web:thread-a", 0, "user", "第一条")
    archive.append_turn("web:thread-b", 0, "user", "第二条")

    summaries = archive.list_sessions(limit=10)

    assert [item["session_id"] for item in summaries][:2] == ["web:thread-b", "web:thread-a"]
    assert summaries[0]["last_message"] == "第二条"


def test_session_archive_reserves_inbound_messages_idempotently_across_reloads(tmp_path: Path) -> None:
    """同一条渠道消息即使跨 SessionArchive 实例重建，也只能被受理一次。"""

    db_path = tmp_path / "agent.db"
    first_archive = SessionArchive(db_path=db_path)

    first_reserved = first_archive.reserve_inbound_message(
        channel="feishu",
        channel_instance="feishu-main",
        channel_message_id="om-msg-1",
        session_id="chat-1",
        thread_key="feishu:feishu-main:chat-1",
        sender="user-1",
        content="我今天中午吃啥了",
        run_id="run-1",
        payload={"source_message_id": "om-msg-1"},
    )

    second_archive = SessionArchive(db_path=db_path)
    second_reserved = second_archive.reserve_inbound_message(
        channel="feishu",
        channel_instance="feishu-main",
        channel_message_id="om-msg-1",
        session_id="chat-1",
        thread_key="feishu:feishu-main:chat-1",
        sender="user-1",
        content="我今天中午吃啥了",
        run_id="run-2",
        payload={"source_message_id": "om-msg-1"},
    )

    stored = second_archive.get_channel_message(
        channel="feishu",
        channel_instance="feishu-main",
        direction="inbound",
        channel_message_id="om-msg-1",
    )

    assert first_reserved is True
    assert second_reserved is False
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["run_id"] == "run-1"


def test_session_archive_rejects_legacy_inbound_replay_seen_only_in_session_history(tmp_path: Path) -> None:
    """历史会话里已有同 id 用户消息时，即使缺少渠道去重记录，也不应再次受理。"""

    archive = SessionArchive(db_path=tmp_path / "agent.db")
    now = datetime.now(UTC)
    session = Session(
        metadata=SessionMetadata(
            session_id="feishu:chat-legacy",
            channel="feishu",
            created_at=now,
            last_active_at=now,
        ),
        history=[
            {
                "id": "om-msg-legacy",
                "role": "user",
                "content": "我中午吃了老乡鸡",
            },
            {
                "id": "assistant-legacy",
                "role": "assistant",
                "content": "记住了。",
            },
        ],
    )
    archive.persist_session(session)

    reserved = archive.reserve_inbound_message(
        channel="feishu",
        channel_instance="feishu-main",
        channel_message_id="om-msg-legacy",
        session_id="feishu:chat-legacy",
        thread_key="feishu:feishu-main:chat-legacy",
        sender="user-legacy",
        content="我中午吃了老乡鸡",
        run_id="run-replay",
        payload={"source_message_id": "om-msg-legacy"},
    )

    stored = archive.get_channel_message(
        channel="feishu",
        channel_instance="feishu-main",
        direction="inbound",
        channel_message_id="om-msg-legacy",
    )

    assert reserved is False
    assert stored is None


def test_session_archive_allows_failed_inbound_message_to_retry(tmp_path: Path) -> None:
    """失败的入站消息应允许后续重试，而不是永远卡死。"""

    archive = SessionArchive(db_path=tmp_path / "agent.db")
    reserved = archive.reserve_inbound_message(
        channel="feishu",
        channel_instance="feishu-main",
        channel_message_id="om-msg-retry",
        session_id="chat-2",
        thread_key="feishu:feishu-main:chat-2",
        sender="user-2",
        content="帮我记一笔午饭",
        run_id="run-first",
    )
    archive.mark_channel_message_status(
        channel="feishu",
        channel_instance="feishu-main",
        direction="inbound",
        channel_message_id="om-msg-retry",
        status="failed",
        run_id="run-first",
        error_message="provider timeout",
    )

    retried = archive.reserve_inbound_message(
        channel="feishu",
        channel_instance="feishu-main",
        channel_message_id="om-msg-retry",
        session_id="chat-2",
        thread_key="feishu:feishu-main:chat-2",
        sender="user-2",
        content="帮我记一笔午饭",
        run_id="run-second",
    )
    stored = archive.get_channel_message(
        channel="feishu",
        channel_instance="feishu-main",
        direction="inbound",
        channel_message_id="om-msg-retry",
    )

    assert reserved is True
    assert retried is True
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["run_id"] == "run-second"
    assert stored["attempt_count"] == 2


def test_session_archive_can_upsert_outbound_channel_message_with_reply_association(tmp_path: Path) -> None:
    """出站消息应能记录 reply 关联，并在同一 message_id 上持续更新。"""

    archive = SessionArchive(db_path=tmp_path / "agent.db")

    message_key = archive.upsert_channel_message(
        direction="outbound",
        role="assistant",
        channel="feishu",
        channel_instance="feishu-main",
        session_id="chat-3",
        thread_key="feishu:feishu-main:chat-3",
        channel_message_id="bot-msg-1",
        reply_to_channel_message_id="om-msg-3",
        caused_by_message_id="run-3",
        run_id="run-3",
        content="👀 已收到，正在思考…",
        status="ack_sent",
        payload={"kind": "placeholder"},
    )
    archive.upsert_channel_message(
        direction="outbound",
        role="assistant",
        channel="feishu",
        channel_instance="feishu-main",
        session_id="chat-3",
        thread_key="feishu:feishu-main:chat-3",
        channel_message_id="bot-msg-1",
        reply_to_channel_message_id="om-msg-3",
        caused_by_message_id="run-3",
        run_id="run-3",
        content="今天中午你吃的是老乡鸡。",
        status="completed",
        payload={"kind": "final"},
    )

    stored = archive.get_channel_message(
        channel="feishu",
        channel_instance="feishu-main",
        direction="outbound",
        channel_message_id="bot-msg-1",
    )

    assert message_key
    assert stored is not None
    assert stored["message_key"] == message_key
    assert stored["reply_to_channel_message_id"] == "om-msg-3"
    assert stored["content"] == "今天中午你吃的是老乡鸡。"
    assert stored["status"] == "completed"
