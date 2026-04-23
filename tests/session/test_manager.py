"""SessionManager 测试。"""

from pathlib import Path

import pytest

from agent.session.manager import SessionManager


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
