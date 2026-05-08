"""面向 Always-On Memory 的工具函数。"""

from datetime import datetime


def profile_write(always_on_memory, content: str) -> str:
    """替换 PROFILE.md 的内容。"""

    _require_dependency(always_on_memory, "AlwaysOnMemory")
    always_on_memory.replace_profile(content)
    return "ok"


def memory_write(always_on_memory, content: str) -> str:
    """兼容旧名称：替换 PROFILE.md 的内容。"""

    return profile_write(always_on_memory, content)


def memory_search(memory_store, mem0_memory_service=None, *, query: str, limit: int = 5, context=None) -> str:
    """Search auditable durable memory items."""

    mem0_rows = _search_mem0(mem0_memory_service, query=query, limit=limit, context=context)
    if mem0_rows is not None:
        if not mem0_rows:
            return "No memories found."
        return "\n".join(_format_mem0_row(row) for row in mem0_rows)

    _require_dependency(memory_store, "MemoryStore")
    rows = memory_store.search(query, limit=limit)
    if not rows:
        return "No memories found."
    return "\n".join(_format_memory_row(row) for row in rows)


def memory_list_recent(memory_store, mem0_memory_service=None, *, limit: int = 10, context=None) -> str:
    """List recent auditable durable memory items."""

    mem0_rows = _list_mem0_recent(mem0_memory_service, limit=limit, context=context)
    if mem0_rows is not None:
        if not mem0_rows:
            return "No recent memories."
        return "\n".join(_format_mem0_row(row) for row in mem0_rows)

    _require_dependency(memory_store, "MemoryStore")
    rows = memory_store.list_recent(limit=limit)
    if not rows:
        return "No recent memories."
    return "\n".join(_format_memory_row(row) for row in rows)


def memory_forget(memory_store, mem0_memory_service=None, *, memory_id: str, context=None) -> str:
    """Mark one durable memory item obsolete."""

    if mem0_memory_service is not None and mem0_memory_service.is_ready:
        outcome = mem0_memory_service.delete(memory_id)
        if outcome.get("ok"):
            return "ok"

    _require_dependency(memory_store, "MemoryStore")
    return "ok" if memory_store.mark_obsolete(memory_id) else "Memory not found."


def _require_dependency(dependency, name: str) -> None:
    """在工具真正执行前检查依赖是否已经注入。"""

    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")


def _format_memory_row(row: dict) -> str:
    return (
        f"[{row.get('id')}] {row.get('kind')} / {row.get('importance')}: "
        f"{row.get('title')} - {row.get('content')}"
    )


def _search_mem0(mem0_memory_service, *, query: str, limit: int, context):
    if mem0_memory_service is None or not mem0_memory_service.is_ready or context is None:
        return None
    outcome = mem0_memory_service.search(
        query,
        user_id=context.sender or "unknown",
        run_id=context.thread_key,
        top_k=limit,
    )
    if not outcome.get("ok"):
        return None
    return outcome.get("results") or []


def _list_mem0_recent(mem0_memory_service, *, limit: int, context):
    if mem0_memory_service is None or not mem0_memory_service.is_ready or context is None:
        return None
    outcome = mem0_memory_service.get_all(user_id=context.sender or "unknown", top_k=limit)
    if not outcome.get("ok"):
        return None
    rows = outcome.get("results") or []
    rows.sort(key=_mem0_row_sort_key, reverse=True)
    return rows[:limit]


def _format_mem0_row(row: dict) -> str:
    memory_id = row.get("id") or "unknown"
    return f"[{memory_id}] {_mem0_row_text(row)}"


def _mem0_row_text(row: dict) -> str:
    for key in ("memory", "content", "text", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "(empty memory)"


def _mem0_row_sort_key(row: dict) -> tuple[int, str]:
    for key in ("updated_at", "created_at"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        return (1, parsed.isoformat())
    return (0, str(row.get("id") or ""))
