"""结构化账本存储。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class LedgerStore:
    """在统一 agent.db 中保存记账草稿与正式账目。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def upsert_draft(
        self,
        *,
        thread_id: str,
        source_message_id: str | None,
        direction: str | None,
        amount_cent: int | None,
        currency: str | None,
        category: str | None,
        occurred_at: str | None,
        merchant: str | None,
        note: str | None,
        missing_fields: list[str],
    ) -> None:
        updated_at = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO ledger_drafts("
                "thread_id, source_message_id, direction, amount_cent, currency, category, "
                "occurred_at, merchant, note, missing_fields_json, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET "
                "source_message_id=excluded.source_message_id, "
                "direction=excluded.direction, "
                "amount_cent=excluded.amount_cent, "
                "currency=excluded.currency, "
                "category=excluded.category, "
                "occurred_at=excluded.occurred_at, "
                "merchant=excluded.merchant, "
                "note=excluded.note, "
                "missing_fields_json=excluded.missing_fields_json, "
                "updated_at=excluded.updated_at",
                (
                    thread_id,
                    source_message_id,
                    direction,
                    amount_cent,
                    currency,
                    category,
                    occurred_at,
                    merchant,
                    note,
                    json.dumps(missing_fields, ensure_ascii=False),
                    updated_at,
                ),
            )

    def get_active_draft(self, thread_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT thread_id, source_message_id, direction, amount_cent, currency, category, "
                "occurred_at, merchant, note, missing_fields_json, updated_at "
                "FROM ledger_drafts WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()

        if row is None:
            return None

        result = dict(row)
        result["missing_fields"] = json.loads(result.pop("missing_fields_json") or "[]")
        return result

    def commit_draft(self, thread_id: str) -> str:
        draft = self.get_active_draft(thread_id)
        if draft is None:
            raise ValueError("No active draft found")
        if draft["missing_fields"]:
            raise ValueError("Ledger draft is incomplete")

        entry_id = self.add_entry(
            direction=draft["direction"],
            amount_cent=draft["amount_cent"],
            currency=draft["currency"] or "CNY",
            category=draft["category"],
            occurred_at=draft["occurred_at"],
            merchant=draft["merchant"],
            note=draft["note"],
            source_message_id=draft["source_message_id"],
            source_thread_id=thread_id,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM ledger_drafts WHERE thread_id = ?", (thread_id,))

        return entry_id

    def add_entry(
        self,
        *,
        direction: str,
        amount_cent: int,
        currency: str,
        category: str | None,
        occurred_at: str,
        merchant: str | None,
        note: str | None,
        source_message_id: str | None,
        source_thread_id: str | None,
    ) -> str:
        entry_id = str(uuid4())
        created_at = _utcnow_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO ledger_entries("
                "id, direction, amount_cent, currency, category, occurred_at, merchant, note, "
                "source_message_id, source_thread_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    direction,
                    amount_cent,
                    currency,
                    category,
                    occurred_at,
                    merchant,
                    note,
                    source_message_id,
                    source_thread_id,
                    created_at,
                    created_at,
                ),
            )
        return entry_id

    def query_entries(
        self,
        *,
        direction: str | None = None,
        category: str | None = None,
        source_thread_id: str | None = None,
        occurred_from: str | None = None,
        occurred_to: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        clauses = []
        params: list[object] = []
        if direction:
            clauses.append("direction = ?")
            params.append(direction)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if source_thread_id:
            clauses.append("source_thread_id = ?")
            params.append(source_thread_id)
        if occurred_from:
            clauses.append("occurred_at >= ?")
            params.append(occurred_from)
        if occurred_to:
            clauses.append("occurred_at < ?")
            params.append(occurred_to)

        sql = (
            "SELECT id, direction, amount_cent, currency, category, occurred_at, merchant, note, "
            "source_message_id, source_thread_id, created_at, updated_at "
            "FROM ledger_entries"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at DESC, created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def summary(
        self,
        *,
        category: str | None = None,
        source_thread_id: str | None = None,
        occurred_from: str | None = None,
        occurred_to: str | None = None,
    ) -> dict:
        clauses = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if source_thread_id:
            clauses.append("source_thread_id = ?")
            params.append(source_thread_id)
        if occurred_from:
            clauses.append("occurred_at >= ?")
            params.append(occurred_from)
        if occurred_to:
            clauses.append("occurred_at < ?")
            params.append(occurred_to)

        sql = (
            "SELECT "
            "COUNT(*) AS entry_count, "
            "COALESCE(SUM(CASE WHEN direction = 'expense' THEN amount_cent ELSE 0 END), 0) AS expense_cent, "
            "COALESCE(SUM(CASE WHEN direction = 'income' THEN amount_cent ELSE 0 END), 0) AS income_cent "
            "FROM ledger_entries"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()

        expense_cent = row["expense_cent"]
        income_cent = row["income_cent"]
        return {
            "entry_count": row["entry_count"],
            "expense_cent": expense_cent,
            "income_cent": income_cent,
            "net_cent": income_cent - expense_cent,
        }

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger_drafts ("
                "thread_id TEXT PRIMARY KEY, "
                "source_message_id TEXT, "
                "direction TEXT, "
                "amount_cent INTEGER, "
                "currency TEXT, "
                "category TEXT, "
                "occurred_at TEXT, "
                "merchant TEXT, "
                "note TEXT, "
                "missing_fields_json TEXT NOT NULL DEFAULT '[]', "
                "updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger_entries ("
                "id TEXT PRIMARY KEY, "
                "direction TEXT NOT NULL, "
                "amount_cent INTEGER NOT NULL, "
                "currency TEXT NOT NULL, "
                "category TEXT, "
                "occurred_at TEXT NOT NULL, "
                "merchant TEXT, "
                "note TEXT, "
                "source_message_id TEXT, "
                "source_thread_id TEXT, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
