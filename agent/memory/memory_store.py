"""Auditable long-term memory store."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class MemoryStore:
    """Persist durable memory items in the workspace database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def add_item(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        confidence: float = 0.8,
        importance: str = "medium",
        subject_id: str = "default",
        source_thread_id: str | None = None,
        source_message_id: str | None = None,
        source_sender_id: str | None = None,
        supersedes_id: str | None = None,
    ) -> str:
        memory_id = str(uuid4())
        now = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memory_items("
                "id, subject_id, source_sender_id, kind, title, content, confidence, importance, "
                "source_thread_id, source_message_id, status, supersedes_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    subject_id,
                    source_sender_id,
                    kind,
                    title,
                    content,
                    confidence,
                    importance,
                    source_thread_id,
                    source_message_id,
                    "active",
                    supersedes_id,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO memory_items_fts(rowid, id, kind, title, content) "
                "VALUES ((SELECT rowid FROM memory_items WHERE id = ?), ?, ?, ?, ?)",
                (memory_id, memory_id, kind, title, content),
            )
        return memory_id

    def search(self, query: str, *, limit: int = 5, kind: str | None = None) -> list[dict]:
        pattern = f"%{query}%"
        sql = (
            "SELECT id, subject_id, source_sender_id, kind, title, content, confidence, importance, "
            "source_thread_id, source_message_id, status, supersedes_id, created_at, updated_at "
            "FROM memory_items "
            "WHERE status = 'active' AND (title LIKE ? OR content LIKE ?)"
        )
        params: list[object] = [pattern, pattern]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return self._fetch_dicts(sql, params)

    def list_recent(self, limit: int = 20, *, kind: str | None = None) -> list[dict]:
        sql = (
            "SELECT id, subject_id, source_sender_id, kind, title, content, confidence, importance, "
            "source_thread_id, source_message_id, status, supersedes_id, created_at, updated_at "
            "FROM memory_items WHERE status = 'active'"
        )
        params: list[object] = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return self._fetch_dicts(sql, params)

    def mark_obsolete(self, memory_id: str) -> bool:
        now = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE memory_items SET status = 'obsolete', updated_at = ? WHERE id = ?",
                (now, memory_id),
            )
            conn.execute("DELETE FROM memory_items_fts WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0

    def replace_item(
        self,
        memory_id: str,
        *,
        kind: str,
        title: str,
        content: str,
        confidence: float = 0.8,
        importance: str = "medium",
        subject_id: str = "default",
        source_thread_id: str | None = None,
        source_message_id: str | None = None,
        source_sender_id: str | None = None,
    ) -> str:
        self.mark_obsolete(memory_id)
        return self.add_item(
            kind=kind,
            title=title,
            content=content,
            confidence=confidence,
            importance=importance,
            subject_id=subject_id,
            source_thread_id=source_thread_id,
            source_message_id=source_message_id,
            source_sender_id=source_sender_id,
            supersedes_id=memory_id,
        )

    def _fetch_dicts(self, sql: str, params: list[object]) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_items ("
                "id TEXT PRIMARY KEY, "
                "subject_id TEXT NOT NULL DEFAULT 'default', "
                "source_sender_id TEXT, "
                "kind TEXT NOT NULL, "
                "title TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "confidence REAL NOT NULL DEFAULT 0.8, "
                "importance TEXT NOT NULL DEFAULT 'medium', "
                "source_thread_id TEXT, "
                "source_message_id TEXT, "
                "status TEXT NOT NULL DEFAULT 'active', "
                "supersedes_id TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
            if "source_sender_id" not in columns:
                conn.execute("ALTER TABLE memory_items ADD COLUMN source_sender_id TEXT")
            conn.execute("DROP TABLE IF EXISTS memory_items_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5("
                "id UNINDEXED, kind UNINDEXED, title, content)"
            )
            conn.execute(
                "INSERT INTO memory_items_fts(rowid, id, kind, title, content) "
                "SELECT rowid, id, kind, title, content FROM memory_items WHERE status = 'active'"
            )


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
