"""基于 SQLite + FTS5 的会话归档。

这里承担两个职责：
1. 把会话历史落成可查询的本地数据库
2. 提供一个足够轻量的全文检索入口

一期先追求“本地可用、可检索、可维护”，不追求复杂索引策略。
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3


class SessionArchive:
    """会话归档与检索组件。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def append_turn(
        self,
        session_id: str,
        turn_index: int,
        role: str,
        content: str,
        *,
        payload: dict | None = None,
        recorded_at: str | None = None,
    ) -> None:
        """写入或覆盖某一轮消息。

        这里同时更新主表和 FTS 表，
        保证后续 `search()` 能查到最新内容。
        """

        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        recorded_at = recorded_at or datetime.now(UTC).isoformat()

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
                "INSERT INTO sessions(session_id, turn_index, role, content, payload, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, turn_index, role, content, payload_json, recorded_at),
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
                content=self._searchable_content(message),
                payload=message,
            )

    def load_session(self, session_id: str):
        """从归档里恢复完整会话对象。"""

        from agent.session.models import Session, SessionMetadata

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, payload, recorded_at "
                "FROM sessions "
                "WHERE session_id = ? "
                "ORDER BY turn_index ASC",
                (session_id,),
            ).fetchall()

        if not rows:
            return None

        history: list[dict] = []
        for row in rows:
            payload = row["payload"]
            if payload:
                history.append(json.loads(payload))
            else:
                history.append({"role": row["role"], "content": row["content"]})

        created_at = self._parse_dt(rows[0]["recorded_at"])
        last_active_at = self._parse_dt(rows[-1]["recorded_at"])
        return Session(
            metadata=SessionMetadata(
                session_id=session_id,
                channel=self._infer_channel(session_id),
                created_at=created_at,
                last_active_at=last_active_at,
                message_count=len(history),
            ),
            history=history,
        )

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """按最近活跃时间返回线程摘要。"""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, "
                "COUNT(*) AS message_count, "
                "MIN(recorded_at) AS created_at, "
                "MAX(recorded_at) AS updated_at, "
                "("
                "  SELECT content FROM sessions latest "
                "  WHERE latest.session_id = sessions.session_id "
                "  ORDER BY latest.turn_index DESC "
                "  LIMIT 1"
                ") AS last_message "
                "FROM sessions "
                "GROUP BY session_id "
                "ORDER BY updated_at DESC, session_id DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()

        return [
            {
                "session_id": row["session_id"],
                "channel": self._infer_channel(row["session_id"]),
                "message_count": row["message_count"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_message": row["last_message"] or "",
            }
            for row in rows
        ]

    def _init_db(self) -> None:
        """初始化数据库结构，并重建 FTS 索引。"""

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "session_id TEXT NOT NULL, "
                "turn_index INTEGER NOT NULL, "
                "role TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "payload TEXT, "
                "recorded_at TEXT NOT NULL DEFAULT '', "
                "PRIMARY KEY (session_id, turn_index))"
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "payload" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN payload TEXT")
            if "recorded_at" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN recorded_at TEXT NOT NULL DEFAULT ''")
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

    def _searchable_content(self, message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(part for part in text_parts if part)
        return str(content)

    def _infer_channel(self, session_id: str) -> str:
        if ":" in session_id:
            return session_id.split(":", 1)[0]
        return "unknown"

    def _parse_dt(self, value: str) -> datetime:
        if not value:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
