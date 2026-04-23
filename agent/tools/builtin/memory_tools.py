"""面向 Always-On Memory 的工具函数。"""

def memory_write(always_on_memory, content: str) -> str:
    """替换 MEMORY.md 的内容。"""

    _require_dependency(always_on_memory, "AlwaysOnMemory")
    always_on_memory.replace_memory(content)
    return "ok"


def _require_dependency(dependency, name: str) -> None:
    """在工具真正执行前检查依赖是否已经注入。"""

    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")
