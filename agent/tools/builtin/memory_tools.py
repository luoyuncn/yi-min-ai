"""面向 Always-On Memory 和 M-flow 的工具函数。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor


def memory_write(always_on_memory, content: str) -> str:
    """替换 MEMORY.md 的内容。"""

    _require_dependency(always_on_memory, "AlwaysOnMemory")
    always_on_memory.replace_memory(content)
    return "ok"


def memory_search(memory_store, query: str, limit: int = 5) -> str:
    """Search auditable durable memory items."""

    _require_dependency(memory_store, "MemoryStore")
    rows = memory_store.search(query, limit=limit)
    if not rows:
        return "No memories found."
    return "\n".join(_format_memory_row(row) for row in rows)


def memory_list_recent(memory_store, limit: int = 10) -> str:
    """List recent auditable durable memory items."""

    _require_dependency(memory_store, "MemoryStore")
    rows = memory_store.list_recent(limit=limit)
    if not rows:
        return "No recent memories."
    return "\n".join(_format_memory_row(row) for row in rows)


def memory_forget(memory_store, memory_id: str) -> str:
    """Mark one durable memory item obsolete."""

    _require_dependency(memory_store, "MemoryStore")
    return "ok" if memory_store.mark_obsolete(memory_id) else "Memory not found."


def recall_memory(mflow_bridge, question: str, top_k: int = 3) -> str:
    """深度记忆检索（M-flow 图路由）。

    适用于需要因果推理、跨会话关联的复杂问题。
    例如："为什么上周我决定不用 Redis？""上次提到的那个性能问题后来怎样了？"

    Args:
        mflow_bridge: MflowBridge 实例
        question: 检索问题
        top_k: 返回的 Episode 数量（默认 3）

    Returns:
        格式化的 Episode bundles 文本
    """
    _require_dependency(mflow_bridge, "MflowBridge")

    try:
        asyncio.get_running_loop()
    except Exception as e:
        try:
            bundles = asyncio.run(mflow_bridge.query(question, top_k))
        except Exception as inner_exc:
            return f"Memory retrieval failed: {str(inner_exc)}"
    else:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                bundles = executor.submit(
                    lambda: asyncio.run(mflow_bridge.query(question, top_k))
                ).result(timeout=10)
        except Exception as inner_exc:
            return f"Memory retrieval failed: {str(inner_exc)}"

    if not bundles:
        return "No relevant memories found."

    # 格式化返回结果
    parts = [f"Found {len(bundles)} relevant episodes:\n"]
    for i, bundle in enumerate(bundles, 1):
        parts.append(f"\n## Episode {i} (score: {bundle.score:.3f})")
        parts.append(f"**Summary:** {bundle.summary}")
        parts.append(f"**Created:** {bundle.created_at.strftime('%Y-%m-%d %H:%M')}")

        if bundle.entities:
            entities = ", ".join(e.get("name", "?") for e in bundle.entities[:5])
            parts.append(f"**Entities:** {entities}")

        if bundle.facets:
            parts.append(f"**Facets:** {len(bundle.facets)} dimensions")

    return "\n".join(parts)


def _require_dependency(dependency, name: str) -> None:
    """在工具真正执行前检查依赖是否已经注入。"""

    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")


def _format_memory_row(row: dict) -> str:
    return (
        f"[{row.get('id')}] {row.get('kind')} / {row.get('importance')}: "
        f"{row.get('title')} - {row.get('content')}"
    )
