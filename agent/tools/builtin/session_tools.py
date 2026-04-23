"""和 Session Archive、Skill Loader 相关的工具函数。"""

def search_sessions(session_archive, query: str, limit: int = 5) -> str:
    """查询 SQLite 归档，并把结果整理成模型易读的文本。"""

    _require_dependency(session_archive, "SessionArchive")
    rows = session_archive.search(query, limit=limit)
    if not rows:
        return "No archived sessions found."
    return "\n".join(f"[{row['session_id']}#{row['turn_index']}] {row['role']}: {row['content']}" for row in rows)


def read_skill(skill_loader, skill_name: str) -> str:
    """读取单个技能全文。"""

    _require_dependency(skill_loader, "SkillLoader")
    return skill_loader.read_full(skill_name)


def _require_dependency(dependency, name: str) -> None:
    """在调用前确认相关依赖已经配置好。"""

    if dependency is None:
        raise RuntimeError(f"{name} dependency is not configured")
