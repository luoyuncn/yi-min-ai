import json
from pathlib import Path

from agent.fitness.file_store import FitnessFileStore
from agent.tools.builtin.fitness_tools import (
    fitness_audit_recent,
    fitness_profile_get,
    fitness_profile_update,
    fitness_settings_get,
    fitness_settings_update,
    fitness_workout_append,
    fitness_workout_recent,
)


def test_fitness_tools_can_update_profile_append_workout_and_read_audit(tmp_path: Path) -> None:
    store = FitnessFileStore(tmp_path)

    updated = fitness_profile_update(
        store,
        name="腿哥",
        goal="力量提升",
        level="有基础",
        plan_style="PPL",
        primary_coach="凯圣王×谭指导",
    )

    assert "Updated fitness profile" in updated
    profile_text = fitness_profile_get(store)
    assert "腿哥" in profile_text
    assert "力量提升" in profile_text
    assert "凯圣王×谭指导" in profile_text

    settings_updated = fitness_settings_update(
        store,
        rpg_enabled=True,
        story_density="low",
        world_name="风痕原野",
    )
    assert "Updated fitness settings" in settings_updated
    settings_text = fitness_settings_get(store)
    assert "风痕原野" in settings_text

    append_result = fitness_workout_append(
        store,
        title="推类日",
        exercises=["卧推 60kg x 5 x 3", "上斜哑铃 20kg x 10 x 3"],
        duration_minutes=55,
        rpe=8,
        readiness="状态不错",
        notes="胸和三头发力清晰",
        source_thread_id="feishu:chat-1",
        source_message_id="msg-1",
    )
    assert "Appended workout" in append_result

    recent_text = fitness_workout_recent(store, limit=5)
    assert "推类日" in recent_text
    assert "卧推 60kg x 5 x 3" in recent_text

    audit_text = fitness_audit_recent(store, limit=10)
    assert "profile_updated" in audit_text
    assert "settings_updated" in audit_text
    assert "workout_appended" in audit_text


def test_fitness_store_scaffolds_expected_files(tmp_path: Path) -> None:
    store = FitnessFileStore(tmp_path)

    assert (tmp_path / "fitness" / "PROFILE.json").exists()
    assert (tmp_path / "fitness" / "SETTINGS.json").exists()
    assert (tmp_path / "fitness" / "PLAN.md").exists()
    assert (tmp_path / "fitness" / "STORY.md").exists()
    assert (tmp_path / "fitness" / "WORLD.md").exists()
    assert (tmp_path / "fitness" / "audit" / "events.ndjson").exists()

    profile = json.loads((tmp_path / "fitness" / "PROFILE.json").read_text(encoding="utf-8"))
    assert "training_profile" in profile
    assert "coach_settings" in profile
