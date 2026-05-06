"""Controlled assistant identity storage and SOUL.md rendering."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class IdentityStore:
    """Persist structured assistant identity and render `SOUL.md`."""

    _row_id = "default"

    def __init__(self, db_path: Path, soul_file: Path) -> None:
        self.db_path = Path(db_path)
        self.soul_file = Path(soul_file)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.soul_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def replace_identity(
        self,
        *,
        name: str,
        backstory: str,
        style: str,
        principles: list[str],
    ) -> None:
        updated_at = _utcnow_iso()
        principles_json = json.dumps(principles, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO assistant_identity(id, name, backstory, style, principles_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, backstory=excluded.backstory, style=excluded.style, "
                "principles_json=excluded.principles_json, updated_at=excluded.updated_at",
                (self._row_id, name, backstory, style, principles_json, updated_at),
            )
        self.soul_file.write_text(
            _render_soul_markdown(
                name=name,
                backstory=backstory,
                style=style,
                principles=principles,
            ),
            encoding="utf-8",
        )

    def get_identity(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT name, backstory, style, principles_json FROM assistant_identity WHERE id = ?",
                (self._row_id,),
            ).fetchone()
        if row is None:
            return _read_identity_from_soul_file(self.soul_file)
        return {
            "name": row["name"],
            "backstory": row["backstory"],
            "style": row["style"],
            "principles": json.loads(row["principles_json"] or "[]"),
        }

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assistant_identity ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "backstory TEXT NOT NULL, "
                "style TEXT NOT NULL, "
                "principles_json TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )


def _render_soul_markdown(*, name: str, backstory: str, style: str, principles: list[str]) -> str:
    lines = [
        "# SOUL.md",
        "",
        "## 身份",
        "",
        f"你是{name}。",
    ]
    if backstory.strip():
        lines.extend(["", backstory.strip()])
    lines.extend(["", "## 风格", "", style.strip() or "保持冷静、清醒、诚实。"])
    lines.extend(["", "## 原则", ""])
    if principles:
        lines.extend(f"- {item}" for item in principles if item.strip())
    else:
        lines.append("- 不编造")
    return "\n".join(lines) + "\n"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_identity_from_soul_file(soul_file: Path) -> dict:
    if not soul_file.exists():
        return {"name": "", "backstory": "", "style": "", "principles": []}

    text = soul_file.read_text(encoding="utf-8")
    sections = _split_markdown_sections(text)
    identity_lines = sections.get("身份", [])
    style_lines = sections.get("风格", [])
    principles_lines = sections.get("原则", []) or sections.get("绝不做的事", [])

    name = ""
    backstory_lines: list[str] = []
    for line in identity_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not name and stripped.startswith("你是") and stripped.endswith(("。", ".")):
            name = stripped.removeprefix("你是").rstrip("。. ")
            continue
        backstory_lines.append(stripped)

    style = "\n".join(line.strip() for line in style_lines if line.strip())
    principles = [
        line.strip()[2:].strip()
        for line in principles_lines
        if line.strip().startswith("- ")
    ]
    return {
        "name": name,
        "backstory": "\n".join(backstory_lines),
        "style": style,
        "principles": principles,
    }


def _split_markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_title: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_title = stripped[3:].strip()
            sections[current_title] = []
            continue
        if current_title is not None:
            sections[current_title].append(line)
    return sections
