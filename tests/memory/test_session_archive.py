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
