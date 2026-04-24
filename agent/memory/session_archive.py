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
from uuid import uuid4


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

    def reserve_inbound_message(
        self,
        *,
        channel: str,
        channel_instance: str,
        channel_message_id: str,
        session_id: str,
        thread_key: str,
        sender: str,
        content: str,
        run_id: str | None = None,
        payload: dict | None = None,
    ) -> bool:
        """尝试占用一条入站渠道消息。

        返回 `True` 代表本次应继续处理；
        返回 `False` 代表这条渠道消息之前已经处理过或正在处理。
        """

        recorded_at = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT message_key, status, attempt_count "
                "FROM channel_messages "
                "WHERE channel = ? AND channel_instance = ? AND direction = 'inbound' "
                "AND channel_message_id = ?",
                (channel, channel_instance, channel_message_id),
            ).fetchone()

            if existing is None:
                if self._session_history_contains_user_message_id(
                    conn,
                    channel_message_id=channel_message_id,
                ):
                    return False
                message_key = self._build_channel_message_key(
                    direction="inbound",
                    channel=channel,
                    channel_instance=channel_instance,
                    channel_message_id=channel_message_id,
                )
                conn.execute(
                    "INSERT INTO channel_messages("
                    "message_key, direction, role, channel, channel_instance, session_id, thread_key, "
                    "channel_message_id, reply_to_channel_message_id, caused_by_message_id, run_id, sender, "
                    "content, payload, status, error_message, attempt_count, recorded_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        message_key,
                        "inbound",
                        "user",
                        channel,
                        channel_instance,
                        session_id,
                        thread_key,
                        channel_message_id,
                        None,
                        None,
                        run_id,
                        sender,
                        content,
                        payload_json,
                        "queued",
                        None,
                        1,
                        recorded_at,
                        recorded_at,
                    ),
                )
                return True

            if existing["status"] == "failed":
                conn.execute(
                    "UPDATE channel_messages SET "
                    "session_id = ?, thread_key = ?, run_id = ?, sender = ?, content = ?, payload = ?, "
                    "status = ?, error_message = NULL, attempt_count = ?, updated_at = ? "
                    "WHERE message_key = ?",
                    (
                        session_id,
                        thread_key,
                        run_id,
                        sender,
                        content,
                        payload_json,
                        "queued",
                        (existing["attempt_count"] or 1) + 1,
                        recorded_at,
                        existing["message_key"],
                    ),
                )
                return True

            return False

    def mark_channel_message_status(
        self,
        *,
        channel: str,
        channel_instance: str,
        direction: str,
        channel_message_id: str,
        status: str,
        run_id: str | None = None,
        content: str | None = None,
        payload: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        """更新一条渠道消息的状态。"""

        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        updated_at = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT message_key, content, payload, run_id, error_message "
                "FROM channel_messages "
                "WHERE channel = ? AND channel_instance = ? AND direction = ? AND channel_message_id = ?",
                (channel, channel_instance, direction, channel_message_id),
            ).fetchone()
            if existing is None:
                return

            conn.execute(
                "UPDATE channel_messages SET "
                "status = ?, run_id = ?, content = ?, payload = ?, error_message = ?, updated_at = ? "
                "WHERE message_key = ?",
                (
                    status,
                    run_id or existing["run_id"],
                    content if content is not None else existing["content"],
                    payload_json if payload_json is not None else existing["payload"],
                    error_message,
                    updated_at,
                    existing["message_key"],
                ),
            )

    def upsert_channel_message(
        self,
        *,
        direction: str,
        role: str,
        channel: str,
        channel_instance: str,
        session_id: str,
        thread_key: str,
        channel_message_id: str | None = None,
        reply_to_channel_message_id: str | None = None,
        caused_by_message_id: str | None = None,
        run_id: str | None = None,
        sender: str | None = None,
        content: str = "",
        status: str = "",
        payload: dict | None = None,
        message_key: str | None = None,
    ) -> str:
        """插入或更新一条渠道消息记录。"""

        payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        recorded_at = datetime.now(UTC).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = None
            if channel_message_id is not None:
                existing = conn.execute(
                    "SELECT message_key FROM channel_messages "
                    "WHERE channel = ? AND channel_instance = ? AND direction = ? AND channel_message_id = ?",
                    (channel, channel_instance, direction, channel_message_id),
                ).fetchone()
            elif message_key is not None:
                existing = conn.execute(
                    "SELECT message_key FROM channel_messages WHERE message_key = ?",
                    (message_key,),
                ).fetchone()

            if existing is None:
                resolved_message_key = message_key or self._build_channel_message_key(
                    direction=direction,
                    channel=channel,
                    channel_instance=channel_instance,
                    channel_message_id=channel_message_id,
                )
                conn.execute(
                    "INSERT INTO channel_messages("
                    "message_key, direction, role, channel, channel_instance, session_id, thread_key, "
                    "channel_message_id, reply_to_channel_message_id, caused_by_message_id, run_id, sender, "
                    "content, payload, status, error_message, attempt_count, recorded_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        resolved_message_key,
                        direction,
                        role,
                        channel,
                        channel_instance,
                        session_id,
                        thread_key,
                        channel_message_id,
                        reply_to_channel_message_id,
                        caused_by_message_id,
                        run_id,
                        sender,
                        content,
                        payload_json,
                        status,
                        None,
                        1,
                        recorded_at,
                        recorded_at,
                    ),
                )
                return resolved_message_key

            resolved_message_key = existing["message_key"]
            conn.execute(
                "UPDATE channel_messages SET "
                "role = ?, session_id = ?, thread_key = ?, reply_to_channel_message_id = ?, "
                "caused_by_message_id = ?, run_id = ?, sender = ?, content = ?, payload = ?, "
                "status = ?, updated_at = ? "
                "WHERE message_key = ?",
                (
                    role,
                    session_id,
                    thread_key,
                    reply_to_channel_message_id,
                    caused_by_message_id,
                    run_id,
                    sender,
                    content,
                    payload_json,
                    status,
                    recorded_at,
                    resolved_message_key,
                ),
            )
            return resolved_message_key

    def get_channel_message(
        self,
        *,
        channel: str,
        channel_instance: str,
        direction: str,
        channel_message_id: str,
    ) -> dict | None:
        """按渠道 message_id 读取一条消息记录。"""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM channel_messages "
                "WHERE channel = ? AND channel_instance = ? AND direction = ? AND channel_message_id = ?",
                (channel, channel_instance, direction, channel_message_id),
            ).fetchone()

        return dict(row) if row is not None else None

    def _session_history_contains_user_message_id(
        self,
        conn: sqlite3.Connection,
        *,
        channel_message_id: str,
    ) -> bool:
        rows = conn.execute(
            "SELECT payload FROM sessions "
            "WHERE role = 'user' AND payload IS NOT NULL AND instr(payload, ?) > 0",
            (channel_message_id,),
        ).fetchall()
        for (payload_json,) in rows:
            if not payload_json:
                continue
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("role") == "user"
                and payload.get("id") == channel_message_id
            ):
                return True
        return False

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
            conn.execute(
                "CREATE TABLE IF NOT EXISTS channel_messages ("
                "message_key TEXT PRIMARY KEY, "
                "direction TEXT NOT NULL, "
                "role TEXT NOT NULL, "
                "channel TEXT NOT NULL, "
                "channel_instance TEXT NOT NULL DEFAULT 'default', "
                "session_id TEXT NOT NULL, "
                "thread_key TEXT NOT NULL, "
                "channel_message_id TEXT, "
                "reply_to_channel_message_id TEXT, "
                "caused_by_message_id TEXT, "
                "run_id TEXT, "
                "sender TEXT, "
                "content TEXT NOT NULL DEFAULT '', "
                "payload TEXT, "
                "status TEXT NOT NULL DEFAULT '', "
                "error_message TEXT, "
                "attempt_count INTEGER NOT NULL DEFAULT 1, "
                "recorded_at TEXT NOT NULL DEFAULT '', "
                "updated_at TEXT NOT NULL DEFAULT '')"
            )
            channel_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(channel_messages)").fetchall()
            }
            for column_name, column_type, default in [
                ("reply_to_channel_message_id", "TEXT", "NULL"),
                ("caused_by_message_id", "TEXT", "NULL"),
                ("run_id", "TEXT", "NULL"),
                ("sender", "TEXT", "NULL"),
                ("content", "TEXT", "''"),
                ("payload", "TEXT", "NULL"),
                ("status", "TEXT", "''"),
                ("error_message", "TEXT", "NULL"),
                ("attempt_count", "INTEGER", "1"),
                ("recorded_at", "TEXT", "''"),
                ("updated_at", "TEXT", "''"),
            ]:
                if column_name not in channel_columns:
                    conn.execute(
                        f"ALTER TABLE channel_messages ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default}"
                        if default not in {"NULL"} and column_type != "TEXT"
                        else (
                            f"ALTER TABLE channel_messages ADD COLUMN {column_name} {column_type} DEFAULT {default}"
                            if default == "NULL"
                            else f"ALTER TABLE channel_messages ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default}"
                        )
                    )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_messages_unique_message "
                "ON channel_messages(channel, channel_instance, direction, channel_message_id)"
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

    def _build_channel_message_key(
        self,
        *,
        direction: str,
        channel: str,
        channel_instance: str,
        channel_message_id: str | None,
    ) -> str:
        if channel_message_id:
            return f"{direction}:{channel}:{channel_instance}:{channel_message_id}"
        return f"{direction}:{channel}:{channel_instance}:{uuid4()}"
