"""SessionManager 测试。"""

from pathlib import Path

import pytest

from agent.memory.session_archive import SessionArchive
from agent.session.manager import SessionManager
from agent.session.models import Session, SessionMetadata
from datetime import UTC, datetime


@pytest.mark.asyncio
async def test_session_manager_reuses_active_session(tmp_path: Path) -> None:
    """同一个 session_id 在同一进程中应复用同一个 Session 对象。"""

    manager = SessionManager(db_path=tmp_path / "sessions.db")

    first = await manager.get_or_create("cli:default", channel="cli")
    second = await manager.get_or_create("cli:default", channel="cli")

    assert first is second
    assert first.metadata.session_id == "cli:default"
    assert first.metadata.channel == "cli"
    assert first.metadata.message_count == 0


@pytest.mark.asyncio
async def test_session_manager_restores_archived_session_when_not_active(tmp_path: Path) -> None:
    """当进程内缓存不存在时，应能从 SQLite 归档恢复线程。"""

    db_path = tmp_path / "sessions.db"
    archive = SessionArchive(db_path=db_path)
    now = datetime.now(UTC)
    session = Session(
        metadata=SessionMetadata(
            session_id="web:restored",
            channel="web",
            created_at=now,
            last_active_at=now,
        ),
        history=[
            {"id": "user-1", "role": "user", "content": "你好"},
            {"id": "assistant-1", "role": "assistant", "content": "你好，我在。"},
        ],
    )
    archive.persist_session(session)

    manager = SessionManager(db_path=db_path)
    restored = await manager.get_or_create("web:restored", channel="web")

    assert restored.metadata.session_id == "web:restored"
    assert restored.metadata.channel == "web"
    assert restored.metadata.message_count == 2
    assert restored.history[1]["content"] == "你好，我在。"


@pytest.mark.asyncio
async def test_session_manager_does_not_reuse_sessions_across_runtime_scoped_thread_keys(tmp_path: Path) -> None:
    """即使外部 chat_id 一样，不同 runtime 的 thread key 也应隔离。"""

    manager = SessionManager(db_path=tmp_path / "sessions.db")

    first = await manager.get_or_create("feishu:feishu-main:oc_same", channel="feishu")
    second = await manager.get_or_create("feishu:feishu-ops:oc_same", channel="feishu")

    assert first is not second
    assert first.metadata.session_id == "feishu:feishu-main:oc_same"
    assert second.metadata.session_id == "feishu:feishu-ops:oc_same"
