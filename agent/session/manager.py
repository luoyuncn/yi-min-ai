"""Session 管理器。

一期版本非常简单：
1. 用字典保存活跃会话
2. 同一个 `session_id` 重复请求时复用同一对象

这样已经足够支持 CLI 单会话连续对话。
"""

from pathlib import Path
from datetime import UTC, datetime

from agent.session.models import Session, SessionMetadata


class SessionManager:
    """管理活跃会话的最小实现。"""

    def __init__(self, db_path: Path) -> None:
        # `db_path` 这里先只是占位保留，方便后面阶段扩展到持久化恢复。
        self.db_path = Path(db_path)
        self._active_sessions: dict[str, Session] = {}

    async def get_or_create(self, session_id: str, channel: str) -> Session:
        """按 session_id 获取或创建会话。

        如果会话已存在，就只刷新最后活跃时间；
        否则创建一个全新的 Session。
        """

        session = self._active_sessions.get(session_id)
        if session is not None:
            session.metadata.last_active_at = datetime.now(UTC)
            return session

        now = datetime.now(UTC)
        session = Session(
            metadata=SessionMetadata(
                session_id=session_id,
                channel=channel,
                created_at=now,
                last_active_at=now,
            )
        )
        self._active_sessions[session_id] = session
        return session
