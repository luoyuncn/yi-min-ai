"""Fitness file-backed tools."""

from __future__ import annotations

import json
from pathlib import Path

from agent.fitness.file_store import FitnessFileStore


def fitness_profile_get(store_or_root) -> str:
    store = _coerce_store(store_or_root)
    return json.dumps(store.get_profile(), ensure_ascii=False, indent=2)


def fitness_profile_update(
    store_or_root,
    *,
    name: str | None = None,
    age: int | None = None,
    height_cm: int | None = None,
    weight_kg: int | None = None,
    goal: str | None = None,
    level: str | None = None,
    equipment: str | None = None,
    schedule: str | None = None,
    preferred_time: str | None = None,
    injuries: str | None = None,
    movement_restrictions: str | None = None,
    plan_style: str | None = None,
    current_program_notes: str | None = None,
    primary_coach: str | None = None,
    coach_mix_rules: str | None = None,
    tone_style: str | None = None,
    explanation_depth: str | None = None,
    encouragement_level: str | None = None,
    interaction_mode: str | None = None,
) -> str:
    store = _coerce_store(store_or_root)
    payload = store.update_profile(
        name=name,
        age=age,
        height_cm=height_cm,
        weight_kg=weight_kg,
        goal=goal,
        level=level,
        equipment=equipment,
        schedule=schedule,
        preferred_time=preferred_time,
        injuries=injuries,
        movement_restrictions=movement_restrictions,
        plan_style=plan_style,
        current_program_notes=current_program_notes,
        primary_coach=primary_coach,
        coach_mix_rules=coach_mix_rules,
        tone_style=tone_style,
        explanation_depth=explanation_depth,
        encouragement_level=encouragement_level,
        interaction_mode=interaction_mode,
    )
    return f"Updated fitness profile: {sorted(_flatten_non_empty(payload).keys())}"


def fitness_settings_get(store_or_root) -> str:
    store = _coerce_store(store_or_root)
    return json.dumps(store.get_settings(), ensure_ascii=False, indent=2)


def fitness_settings_update(
    store_or_root,
    *,
    rpg_enabled: bool | None = None,
    story_density: str | None = None,
    pre_battle_narration: bool | None = None,
    post_battle_narration: bool | None = None,
    attribute_display: bool | None = None,
    title_style: str | None = None,
    world_mode: str | None = None,
    world_name: str | None = None,
    protagonist_mode: str | None = None,
    identity_role: str | None = None,
    core_drive: str | None = None,
) -> str:
    store = _coerce_store(store_or_root)
    payload = store.update_settings(
        rpg_enabled=rpg_enabled,
        story_density=story_density,
        pre_battle_narration=pre_battle_narration,
        post_battle_narration=post_battle_narration,
        attribute_display=attribute_display,
        title_style=title_style,
        world_mode=world_mode,
        world_name=world_name,
        protagonist_mode=protagonist_mode,
        identity_role=identity_role,
        core_drive=core_drive,
    )
    return f"Updated fitness settings: {sorted(_flatten_non_empty(payload).keys())}"


def fitness_workout_append(
    store_or_root,
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
) -> str:
    store = _coerce_store(store_or_root)
    target = store.append_workout(
        title=title,
        exercises=exercises,
        duration_minutes=duration_minutes,
        rpe=rpe,
        readiness=readiness,
        notes=notes,
        occurred_at=occurred_at,
        source_thread_id=source_thread_id,
        source_message_id=source_message_id,
    )
    return f"Appended workout: {target.name}"


def fitness_workout_recent(store_or_root, *, limit: int = 5) -> str:
    store = _coerce_store(store_or_root)
    rows = store.recent_workouts(limit=limit)
    if not rows:
        return "No workout records."
    return "\n\n".join(rows)


def fitness_audit_recent(store_or_root, *, limit: int = 20) -> str:
    store = _coerce_store(store_or_root)
    rows = store.recent_audit_events(limit=limit)
    if not rows:
        return "No fitness audit events."
    return "\n".join(
        f"[{row.get('timestamp', '')}] {row.get('event_type', '')}: "
        f"{json.dumps({k: v for k, v in row.items() if k not in {'timestamp', 'event_type'}}, ensure_ascii=False)}"
        for row in rows
    )


def _coerce_store(store_or_root) -> FitnessFileStore:
    if isinstance(store_or_root, FitnessFileStore):
        return store_or_root
    return FitnessFileStore(Path(store_or_root))


def _flatten_non_empty(payload: dict) -> dict:
    flat: dict[str, object] = {}
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if item is None or item == "":
                continue
            flat[key] = item
    return flat
