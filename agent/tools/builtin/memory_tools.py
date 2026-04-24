"""面向 Always-On Memory 和 M-flow 的工具函数。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor


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
