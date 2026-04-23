"""一期工具注册表。

`build_stage1_registry()` 是这个文件的核心：
它把当前阶段允许暴露给模型的“安全工具子集”一次性注册好。
"""

from functools import partial
from pathlib import Path

from agent.tools.builtin.file_ops import file_read, file_write
from agent.tools.builtin.memory_tools import memory_write
from agent.tools.builtin.session_tools import read_skill, search_sessions
from agent.tools.models import ToolDefinition


class ToolRegistry:
    """保存所有已注册工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """注册单个工具。"""

        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        """返回已注册的工具名列表。"""

        return list(self._tools.keys())

    def get(self, name: str) -> ToolDefinition:
        """按名称取出工具定义。"""

        return self._tools[name]

    def get_schemas(self) -> list[dict]:
        """把所有工具转换成统一的 schema 列表。"""

        return [tool.schema for tool in self._tools.values()]


def build_stage1_registry(
    workspace_dir: Path,
    always_on_memory,
    session_archive,
    skill_loader,
) -> ToolRegistry:
    """构建阶段一默认工具集。

    这里刻意只暴露安全工具：
    - 文件读写
    - MEMORY 写入
    - 会话检索
    - 技能全文读取
    """

    registry = ToolRegistry()
    root = Path(workspace_dir)

    registry.register(
        ToolDefinition(
            name="file_read",
            description="Read a UTF-8 text file from the workspace.",
            schema=_schema("file_read", "Read a text file", {"path": _string_field("Relative path")}),
            handler=partial(file_read, root),
        )
    )
    registry.register(
        ToolDefinition(
            name="file_write",
            description="Write a UTF-8 text file inside the workspace.",
            schema=_schema(
                "file_write",
                "Write a text file",
                {"path": _string_field("Relative path"), "content": _string_field("Text content")},
            ),
            handler=partial(file_write, root),
        )
    )
    registry.register(
        ToolDefinition(
            name="memory_write",
            description="Replace MEMORY.md content for the next turn.",
            schema=_schema("memory_write", "Replace MEMORY.md", {"content": _string_field("Memory content")}),
            handler=partial(memory_write, always_on_memory),
        )
    )
    registry.register(
        ToolDefinition(
            name="search_sessions",
            description="Search archived session content in SQLite.",
            schema=_schema(
                "search_sessions",
                "Search archived sessions",
                {"query": _string_field("Search query"), "limit": _integer_field("Result limit")},
            ),
            handler=partial(search_sessions, session_archive),
        )
    )
    registry.register(
        ToolDefinition(
            name="read_skill",
            description="Read the full content of one skill by name.",
            schema=_schema("read_skill", "Read one skill", {"skill_name": _string_field("Skill name")}),
            handler=partial(read_skill, skill_loader),
        )
    )
    return registry


def _schema(name: str, description: str, properties: dict) -> dict:
    """构造统一的 function-call schema。"""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties.keys()),
                "additionalProperties": False,
            },
        },
    }


def _string_field(description: str) -> dict:
    """字符串字段 schema。"""

    return {"type": "string", "description": description}


def _integer_field(description: str) -> dict:
    """整数字段 schema。"""

    return {"type": "integer", "description": description}
