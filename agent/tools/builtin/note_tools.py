"""长期笔记工具。"""


def note_add(
    note_store,
    *,
    note_type: str,
    title: str,
    content: str,
    importance: str = "medium",
    is_user_explicit: bool = False,
    source_message_id: str | None = None,
    source_thread_id: str | None = None,
) -> str:
    _require_dependency(note_store, "NoteStore")
    note_id = note_store.add_note(
        note_type=note_type,
        title=title,
        content=content,
        importance=importance,
        is_user_explicit=is_user_explicit,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
    )
    return f"Created note: {note_id}"


def note_search(note_store, *, query: str, limit: int = 5) -> str:
    _require_dependency(note_store, "NoteStore")
    rows = note_store.search(query, limit=limit)
    if not rows:
        return "No notes found."
    return "\n".join(f"[{row['note_type']}] {row['title']}: {row['content']}" for row in rows)


def note_list_recent(note_store, *, limit: int = 10) -> str:
    _require_dependency(note_store, "NoteStore")
    rows = note_store.list_recent(limit=limit)
    if not rows:
        return "No recent notes."
    return "\n".join(f"[{row['importance']}] {row['title']}: {row['content']}" for row in rows)


def note_update(
    note_store,
    *,
    note_id: str,
    title: str,
    content: str,
    importance: str,
) -> str:
    _require_dependency(note_store, "NoteStore")
    updated = note_store.update_note(
        note_id,
        title=title,
        content=content,
        importance=importance,
    )
    return "ok" if updated else "Note not found."


def _require_dependency(dependency, name: str) -> None:
    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")
