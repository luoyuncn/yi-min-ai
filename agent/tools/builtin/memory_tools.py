"""面向 Always-On Memory 和 M-flow 的工具函数。"""

import asyncio


def memory_write(always_on_memory, content: str) -> str:
    """替换 MEMORY.md 的内容。"""

    _require_dependency(always_on_memory, "AlwaysOnMemory")
    always_on_memory.replace_memory(content)
    return "ok"


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

    # M-flow query 是异步的，需要在当前事件循环中执行
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果在异步上下文中，直接 await
            import inspect
            if inspect.iscoroutinefunction(recall_memory):
                bundles = asyncio.create_task(mflow_bridge.query(question, top_k))
            else:
                # 同步调用，创建新任务
                bundles = asyncio.run_coroutine_threadsafe(
                    mflow_bridge.query(question, top_k), loop
                ).result(timeout=10)
        else:
            bundles = asyncio.run(mflow_bridge.query(question, top_k))
    except Exception as e:
        return f"Memory retrieval failed: {str(e)}"

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
