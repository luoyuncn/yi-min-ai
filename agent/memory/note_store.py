"""长期笔记存储。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class NoteStore:
    """在统一 agent.db 中保存长期笔记。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def add_note(
        self,
        *,
        note_type: str,
        title: str,
        content: str,
        importance: str,
        is_user_explicit: bool,
        source_message_id: str | None,
        source_thread_id: str | None,
    ) -> str:
        note_id = str(uuid4())
        now = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO notes("
                "id, note_type, title, content, importance, is_user_explicit, "
                "source_message_id, source_thread_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id,
                    note_type,
                    title,
                    content,
                    importance,
                    1 if is_user_explicit else 0,
                    source_message_id,
                    source_thread_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO notes_fts(rowid, id, note_type, title, content) "
                "VALUES ((SELECT rowid FROM notes WHERE id = ?), ?, ?, ?, ?)",
                (note_id, note_id, note_type, title, content),
            )
        return note_id

    def search(self, query: str, limit: int = 5) -> list[dict]:
        wildcard = f"%{query}%"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, note_type, title, content, importance, is_user_explicit, "
                "source_message_id, source_thread_id, created_at, updated_at "
                "FROM notes "
                "WHERE title LIKE ? OR content LIKE ? "
                "ORDER BY updated_at DESC, id DESC "
                "LIMIT ?",
                (wildcard, wildcard, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent(self, limit: int = 10) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, note_type, title, content, importance, is_user_explicit, "
                "source_message_id, source_thread_id, created_at, updated_at "
                "FROM notes ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_note(
        self,
        note_id: str,
        *,
        title: str,
        content: str,
        importance: str,
    ) -> bool:
        updated_at = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE notes SET title = ?, content = ?, importance = ?, updated_at = ? WHERE id = ?",
                (title, content, importance, updated_at, note_id),
            )
            if cursor.rowcount == 0:
                return False
            conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
            row = conn.execute(
                "SELECT rowid, id, note_type, title, content FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
            conn.execute(
                "INSERT INTO notes_fts(rowid, id, note_type, title, content) VALUES (?, ?, ?, ?, ?)",
                (row[0], row[1], row[2], row[3], row[4]),
            )
        return True

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS notes ("
                "id TEXT PRIMARY KEY, "
                "note_type TEXT NOT NULL, "
                "title TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "importance TEXT NOT NULL, "
                "is_user_explicit INTEGER NOT NULL DEFAULT 0, "
                "source_message_id TEXT, "
                "source_thread_id TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            conn.execute("DROP TABLE IF EXISTS notes_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                "id UNINDEXED, note_type UNINDEXED, title, content)"
            )
            conn.execute(
                "INSERT INTO notes_fts(id, note_type, title, content) "
                "SELECT id, note_type, title, content FROM notes"
            )


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
