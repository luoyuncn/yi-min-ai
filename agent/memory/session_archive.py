"""基于 SQLite + FTS5 的会话归档。

这里承担两个职责：
1. 把会话历史落成可查询的本地数据库
2. 提供一个足够轻量的全文检索入口

一期先追求“本地可用、可检索、可维护”，不追求复杂索引策略。
"""

from pathlib import Path
import sqlite3


class SessionArchive:
    """会话归档与检索组件。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def append_turn(self, session_id: str, turn_index: int, role: str, content: str) -> None:
        """写入或覆盖某一轮消息。

        这里同时更新主表和 FTS 表，
        保证后续 `search()` 能查到最新内容。
        """

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM sessions WHERE session_id = ? AND turn_index = ?",
                (session_id, turn_index),
            )
            conn.execute(
                "DELETE FROM sessions_fts WHERE session_id = ? AND turn_index = ?",
                (session_id, turn_index),
            )
            conn.execute(
                "INSERT INTO sessions(session_id, turn_index, role, content) VALUES (?, ?, ?, ?)",
                (session_id, turn_index, role, content),
            )
            conn.execute(
                "INSERT INTO sessions_fts(session_id, turn_index, role, content) VALUES (?, ?, ?, ?)",
                (session_id, turn_index, role, content),
            )

    def search(self, query: str, limit: int) -> list[dict]:
        """使用 FTS5 做全文检索。"""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, turn_index, role, content "
                "FROM sessions_fts "
                "WHERE sessions_fts MATCH ? "
                "ORDER BY turn_index ASC "
                "LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def persist_session(self, session) -> None:
        """把整个会话对象重新刷入归档。

        一期这样做虽然不算最省，但实现最直观，
        对当前 CLI 规模完全够用，也方便你阅读。
        """

        for turn_index, message in enumerate(session.history):
            self.append_turn(
                session_id=session.metadata.session_id,
                turn_index=turn_index,
                role=message["role"],
                content=message.get("content", ""),
            )

    def _init_db(self) -> None:
        """初始化数据库结构，并重建 FTS 索引。"""

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "session_id TEXT NOT NULL, "
                "turn_index INTEGER NOT NULL, "
                "role TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "PRIMARY KEY (session_id, turn_index))"
            )
            conn.execute("DROP TABLE IF EXISTS sessions_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
                "session_id UNINDEXED, "
                "turn_index UNINDEXED, "
                "role UNINDEXED, "
                "content)"
            )
            conn.execute(
                "INSERT INTO sessions_fts(session_id, turn_index, role, content) "
                "SELECT session_id, turn_index, role, content FROM sessions"
            )
