"""File-backed fitness domain storage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class FitnessFileStore:
    """Persist fitness state in workspace-local files with audit logging."""

    PROFILE_PATH = "fitness/PROFILE.json"
    SETTINGS_PATH = "fitness/SETTINGS.json"
    PLAN_PATH = "fitness/PLAN.md"
    STORY_PATH = "fitness/STORY.md"
    WORLD_PATH = "fitness/WORLD.md"
    AUDIT_PATH = "fitness/audit/events.ndjson"
    WORKOUTS_DIR = "fitness/workouts"

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.base_dir = self.workspace_dir / "fitness"
        self.workouts_dir = self.base_dir / "workouts"
        self.audit_dir = self.base_dir / "audit"
        self.ensure_initialized()

    def ensure_initialized(self) -> None:
        self.workouts_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_json(self.profile_path, _default_profile())
        self._ensure_json(self.settings_path, _default_settings())
        self._ensure_text(self.plan_path, _default_plan_markdown())
        self._ensure_text(self.story_path, _default_story_markdown())
        self._ensure_text(self.world_path, _default_world_markdown())
        self._ensure_text(self.audit_path, "")

    @property
    def profile_path(self) -> Path:
        return self.workspace_dir / self.PROFILE_PATH

    @property
    def settings_path(self) -> Path:
        return self.workspace_dir / self.SETTINGS_PATH

    @property
    def plan_path(self) -> Path:
        return self.workspace_dir / self.PLAN_PATH

    @property
    def story_path(self) -> Path:
        return self.workspace_dir / self.STORY_PATH

    @property
    def world_path(self) -> Path:
        return self.workspace_dir / self.WORLD_PATH

    @property
    def audit_path(self) -> Path:
        return self.workspace_dir / self.AUDIT_PATH

    def get_profile(self) -> dict:
        return self._read_json(self.profile_path)

    def update_profile(self, **updates) -> dict:
        payload = self.get_profile()
        training_keys = {
            "name",
            "age",
            "height_cm",
            "weight_kg",
            "goal",
            "level",
            "equipment",
            "schedule",
            "preferred_time",
            "injuries",
            "movement_restrictions",
            "plan_style",
            "current_program_notes",
        }
        coach_keys = {
            "primary_coach",
            "coach_mix_rules",
            "tone_style",
            "explanation_depth",
            "encouragement_level",
            "interaction_mode",
        }
        changed: dict[str, object] = {}
        for key, value in updates.items():
            if value is None:
                continue
            if key in training_keys:
                payload["training_profile"][key] = value
                changed[key] = value
            elif key in coach_keys:
                payload["coach_settings"][key] = value
                changed[key] = value
        self._write_json(self.profile_path, payload)
        if changed:
            self.append_audit("profile_updated", {"updated_fields": changed})
        return payload

    def get_settings(self) -> dict:
        return self._read_json(self.settings_path)

    def update_settings(self, **updates) -> dict:
        payload = self.get_settings()
        rpg_keys = {
            "rpg_enabled",
            "story_density",
            "pre_battle_narration",
            "post_battle_narration",
            "attribute_display",
            "title_style",
        }
        world_keys = {
            "world_mode",
            "world_name",
            "protagonist_mode",
            "identity_role",
            "core_drive",
        }
        changed: dict[str, object] = {}
        for key, value in updates.items():
            if value is None:
                continue
            if key in rpg_keys:
                payload["rpg"][key] = value
                changed[key] = value
            elif key in world_keys:
                payload["world"][key] = value
                changed[key] = value
        self._write_json(self.settings_path, payload)
        if changed:
            self.append_audit("settings_updated", {"updated_fields": changed})
        return payload

    def append_workout(
        self,
        *,
        title: str,
        exercises: list[str],
        duration_minutes: int | None = None,
        rpe: int | None = None,
        readiness: str | None = None,
        notes: str | None = None,
        occurred_at: str | None = None,
        source_thread_id: str | None = None,
        source_message_id: str | None = None,
    ) -> Path:
        timestamp = _coerce_timestamp(occurred_at)
        target = self.workouts_dir / f"{timestamp.strftime('%Y-%m')}.md"
        if not target.exists():
            target.write_text(f"# {timestamp.strftime('%Y-%m')} Workouts\n\n", encoding="utf-8")
        lines = [
            f"## [{timestamp.isoformat()}] {title}",
            f"- exercises:",
            *[f"  - {exercise}" for exercise in exercises],
        ]
        if duration_minutes is not None:
            lines.append(f"- duration_minutes: {duration_minutes}")
        if rpe is not None:
            lines.append(f"- rpe: {rpe}")
        if readiness:
            lines.append(f"- readiness: {readiness}")
        if notes:
            lines.append("- notes:")
            for paragraph in notes.splitlines():
                cleaned = paragraph.strip()
                if cleaned:
                    lines.append(f"  - {cleaned}")
        block = "\n".join(lines) + "\n\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(block)
        self.append_audit(
            "workout_appended",
            {
                "title": title,
                "occurred_at": timestamp.isoformat(),
                "file": str(target.relative_to(self.workspace_dir)),
                "source_thread_id": source_thread_id,
                "source_message_id": source_message_id,
            },
        )
        return target

    def recent_workouts(self, limit: int = 5) -> list[str]:
        sections: list[str] = []
        for file_path in sorted(self.workouts_dir.glob("*.md"), reverse=True):
            content = file_path.read_text(encoding="utf-8")
            chunks = content.split("## [")
            for chunk in reversed(chunks[1:]):
                section = "## [" + chunk.strip()
                if section:
                    sections.append(section)
                if len(sections) >= limit:
                    return sections[:limit]
        return sections[:limit]

    def recent_audit_events(self, limit: int = 20) -> list[dict]:
        lines = [line for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        items = [json.loads(line) for line in lines[-limit:]]
        items.reverse()
        return items

    def append_audit(self, event_type: str, payload: dict) -> None:
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "event_type": event_type,
            **payload,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _ensure_json(self, path: Path, payload: dict) -> None:
        if not path.exists():
            self._write_json(path, payload)

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_text(self, path: Path, content: str) -> None:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def _default_profile() -> dict:
    return {
        "training_profile": {
            "name": "",
            "age": None,
            "height_cm": None,
            "weight_kg": None,
            "goal": "",
            "level": "",
            "equipment": "",
            "schedule": "",
            "preferred_time": "",
            "injuries": "",
            "movement_restrictions": "",
            "plan_style": "",
            "current_program_notes": "",
        },
        "coach_settings": {
            "primary_coach": "",
            "coach_mix_rules": "",
            "tone_style": "standard",
            "explanation_depth": "standard",
            "encouragement_level": "medium",
            "interaction_mode": "session",
        },
    }


def _default_settings() -> dict:
    return {
        "rpg": {
            "rpg_enabled": True,
            "story_density": "low",
            "pre_battle_narration": True,
            "post_battle_narration": True,
            "attribute_display": True,
            "title_style": "open-exploration",
        },
        "world": {
            "world_mode": "default",
            "world_name": "风痕原野",
            "protagonist_mode": "real-user-in-otherworld",
            "identity_role": "旅者",
            "core_drive": "让身体与回响同步，走得更远",
        },
    }


def _default_plan_markdown() -> str:
    return (
        "# Fitness Plan\n\n"
        "## Current Week\n\n"
        "- Day 1:\n"
        "- Day 2:\n"
        "- Day 3:\n"
    )


def _default_story_markdown() -> str:
    return (
        "# Fitness Story\n\n"
        "## Current Chapter\n\n"
        "- 风痕原野：初醒之风\n"
        "- 目标：建立稳定训练节奏\n"
    )


def _default_world_markdown() -> str:
    return (
        "# Fitness World\n\n"
        "## Default World\n\n"
        "- 名称：风痕原野\n"
        "- 氛围：治愈、明亮、风很大的荒野感\n"
        "- 力量体系：回响\n"
    )


def _coerce_timestamp(occurred_at: str | None) -> datetime:
    if not occurred_at:
        return datetime.now().astimezone()
    try:
        return datetime.fromisoformat(occurred_at)
    except ValueError:
        return datetime.now().astimezone()
