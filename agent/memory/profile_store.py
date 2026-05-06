"""Controlled core profile storage and PROFILE.md rendering."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ProfileStore:
    """Persist structured core profile and render `PROFILE.md`."""

    _row_id = "default"

    def __init__(self, db_path: Path, profile_file: Path) -> None:
        self.db_path = Path(db_path)
        self.profile_file = Path(profile_file)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def replace_profile(self, *, display_name: str | None, core_facts: list[str]) -> None:
        updated_at = _utcnow_iso()
        core_facts_json = json.dumps(core_facts, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO core_profile(id, display_name, core_facts_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "display_name=excluded.display_name, "
                "core_facts_json=excluded.core_facts_json, "
                "updated_at=excluded.updated_at",
                (self._row_id, display_name, core_facts_json, updated_at),
            )
        self.profile_file.write_text(
            _render_profile_markdown(display_name=display_name, core_facts=core_facts),
            encoding="utf-8",
        )

    def get_profile(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT display_name, core_facts_json FROM core_profile WHERE id = ?",
                (self._row_id,),
            ).fetchone()
        if row is None:
            return {"display_name": None, "core_facts": []}
        return {
            "display_name": row["display_name"],
            "core_facts": json.loads(row["core_facts_json"] or "[]"),
        }

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS core_profile ("
                "id TEXT PRIMARY KEY, "
                "display_name TEXT, "
                "core_facts_json TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )


def _render_profile_markdown(*, display_name: str | None, core_facts: list[str]) -> str:
    lines = ["# User Profile", ""]
    if display_name and display_name.strip():
        lines.append(f"- 称呼：{display_name.strip()}")
    lines.extend(f"- {item}" for item in core_facts if item.strip())
    return "\n".join(lines).rstrip() + "\n"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
