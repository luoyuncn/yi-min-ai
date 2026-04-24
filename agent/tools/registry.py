"""一期工具注册表。

`build_stage1_registry()` 是这个文件的核心：
它把当前阶段允许暴露给模型的“安全工具子集”一次性注册好。
"""

from functools import partial
from pathlib import Path

from agent.tools.builtin.file_ops import file_read, file_write
from agent.tools.builtin.ledger_tools import (
    ledger_commit_draft,
    ledger_get_active_draft,
    ledger_query_entries,
    ledger_summary,
    ledger_upsert_draft,
)
from agent.tools.builtin.memory_tools import memory_write, recall_memory
from agent.tools.builtin.note_tools import note_add, note_list_recent, note_search, note_update
from agent.tools.builtin.session_tools import read_skill, search_sessions
from agent.tools.builtin.shell_tools import shell_exec
from agent.tools.builtin.web_tools import web_search
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

    def get_index(self) -> str:
        """生成给模型阅读的工具索引。"""

        lines = ["Available Tools:"]
        for tool in self._tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)


def build_stage1_registry(
    workspace_dir: Path,
    always_on_memory,
    session_archive,
    skill_loader,
    mflow_bridge=None,
    ledger_store=None,
    note_store=None,
    enable_shell: bool = False,
    enable_web_search: bool = True,
) -> ToolRegistry:
    """构建阶段一默认工具集。

    基础工具：
    - 文件读写
    - MEMORY 写入
    - 会话检索
    - 技能全文读取
    - M-flow 深度检索（如果可用）

    可选工具（需显式启用）：
    - Shell 执行（需审批）
    - Web 搜索
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
            name="ledger_upsert_draft",
            description="Create or update the active ledger draft for one thread.",
            schema=_schema(
                "ledger_upsert_draft",
                "Create or update one ledger draft",
                {
                    "thread_id": _string_field("Runtime-scoped thread id"),
                    "source_message_id": _optional_string_field("Source message id"),
                    "direction": _optional_string_field("income or expense"),
                    "amount_cent": _optional_integer_field("Amount in cents"),
                    "currency": _optional_string_field("Currency code, default CNY"),
                    "category": _optional_string_field("Category such as meal or salary"),
                    "occurred_at": _optional_string_field("Occurrence datetime in ISO 8601"),
                    "merchant": _optional_string_field("Merchant or counterparty"),
                    "note": _optional_string_field("Freeform note"),
                    "missing_fields": _array_field("List of still-missing required fields"),
                },
                required=["thread_id"],
            ),
            handler=partial(ledger_upsert_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_get_active_draft",
            description="Read the active ledger draft for one thread.",
            schema=_schema(
                "ledger_get_active_draft",
                "Read one ledger draft",
                {"thread_id": _string_field("Runtime-scoped thread id")},
            ),
            handler=partial(ledger_get_active_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_commit_draft",
            description="Commit a complete ledger draft into the ledger.",
            schema=_schema(
                "ledger_commit_draft",
                "Commit one ledger draft",
                {"thread_id": _string_field("Runtime-scoped thread id")},
            ),
            handler=partial(ledger_commit_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_query_entries",
            description="Query committed ledger entries.",
            schema=_schema(
                "ledger_query_entries",
                "Query ledger entries",
                {
                    "direction": _optional_string_field("income or expense"),
                    "category": _optional_string_field("Category filter"),
                    "source_thread_id": _optional_string_field("Optional source thread filter"),
                    "limit": _integer_field("Result limit"),
                },
                required=["limit"],
            ),
            handler=partial(ledger_query_entries, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_summary",
            description="Summarize committed ledger entries.",
            schema=_schema(
                "ledger_summary",
                "Summarize ledger entries",
                {
                    "category": _optional_string_field("Category filter"),
                    "source_thread_id": _optional_string_field("Optional source thread filter"),
                },
                required=[],
            ),
            handler=partial(ledger_summary, ledger_store),
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
            name="note_add",
            description="Create one long-lived note.",
            schema=_schema(
                "note_add",
                "Create one note",
                {
                    "note_type": _string_field("Note type such as preference or plan"),
                    "title": _string_field("Short note title"),
                    "content": _string_field("Full note content"),
                    "importance": _string_field("low, medium, or high"),
                    "is_user_explicit": _boolean_field("Whether the user explicitly asked to save it"),
                    "source_message_id": _optional_string_field("Source message id"),
                    "source_thread_id": _optional_string_field("Source thread id"),
                },
                required=["note_type", "title", "content", "importance", "is_user_explicit"],
            ),
            handler=partial(note_add, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_search",
            description="Search saved long-lived notes.",
            schema=_schema(
                "note_search",
                "Search notes",
                {
                    "query": _string_field("Search query"),
                    "limit": _integer_field("Result limit"),
                },
            ),
            handler=partial(note_search, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_list_recent",
            description="List the most recently updated notes.",
            schema=_schema(
                "note_list_recent",
                "List recent notes",
                {"limit": _integer_field("Result limit")},
            ),
            handler=partial(note_list_recent, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_update",
            description="Update an existing note in place.",
            schema=_schema(
                "note_update",
                "Update one note",
                {
                    "note_id": _string_field("Existing note id"),
                    "title": _string_field("Updated title"),
                    "content": _string_field("Updated content"),
                    "importance": _string_field("Updated importance"),
                },
            ),
            handler=partial(note_update, note_store),
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

    # M-flow 深度检索（可选）
    if mflow_bridge is not None and getattr(mflow_bridge, "is_available", False):
        registry.register(
            ToolDefinition(
                name="recall_memory",
                description=(
                    "Deep memory retrieval using M-flow graph routing. "
                    "Use for complex questions requiring causal reasoning or cross-session associations. "
                    "Example: 'Why did I decide not to use Redis last week?'"
                ),
                schema=_schema(
                    "recall_memory",
                    "Graph-routed deep memory retrieval",
                    {
                        "question": _string_field("Question to search for"),
                        "top_k": _integer_field("Number of episodes to return (default 3)"),
                    },
                ),
                handler=partial(recall_memory, mflow_bridge),
            )
        )

    # Shell 执行（需审批）
    if enable_shell:
        registry.register(
            ToolDefinition(
                name="shell_exec",
                description=(
                    "Execute a shell command in the workspace directory. "
                    "**REQUIRES APPROVAL**. Use for running scripts, system commands, etc."
                ),
                schema=_schema(
                    "shell_exec",
                    "Execute shell command",
                    {
                        "command": _string_field("Shell command to execute"),
                        "timeout": _integer_field("Timeout in seconds (default 30)"),
                    },
                ),
                handler=partial(shell_exec, root),
            )
        )

    # Web 搜索
    if enable_web_search:
        registry.register(
            ToolDefinition(
                name="web_search",
                description=(
                    "Search the web using DuckDuckGo. "
                    "Returns titles, snippets, and URLs."
                ),
                schema=_schema(
                    "web_search",
                    "Web search",
                    {
                        "query": _string_field("Search query"),
                        "num_results": _integer_field("Number of results (default 5)"),
                    },
                ),
                handler=web_search,
            )
        )

    return registry


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    """构造统一的 function-call schema。"""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required if required is not None else list(properties.keys()),
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


def _optional_string_field(description: str) -> dict:
    return {"type": ["string", "null"], "description": description}


def _optional_integer_field(description: str) -> dict:
    return {"type": ["integer", "null"], "description": description}


def _boolean_field(description: str) -> dict:
    return {"type": "boolean", "description": description}


def _array_field(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}
