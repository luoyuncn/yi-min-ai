"""一期工具注册表。

`build_stage1_registry()` 是这个文件的核心：
它把当前阶段允许暴露给模型的“安全工具子集”一次性注册好。
"""

from functools import partial
from pathlib import Path

from agent.tools.builtin.cron_tools import (
    cron_create_task,
    cron_delete_task,
    cron_list_tasks,
    cron_run_now,
    cron_update_task,
)
from agent.tools.builtin.file_ops import file_read, file_write
from agent.tools.builtin.ledger_tools import (
    ledger_commit_draft,
    ledger_get_active_draft,
    ledger_query_entries,
    ledger_summary,
    ledger_upsert_draft,
)
from agent.tools.builtin.memory_tools import memory_forget, memory_list_recent, memory_search, profile_write, recall_memory
from agent.tools.builtin.note_tools import note_add, note_list_recent, note_search, note_update
from agent.tools.builtin.reminder_tools import reminder_create, reminder_delete, reminder_list
from agent.tools.builtin.session_tools import read_skill, search_sessions
from agent.tools.builtin.shell_tools import shell_exec
from agent.tools.builtin.web_tools import web_search
from agent.tools.models import ToolDefinition
from agent.tools.runtime_context import RuntimeServices


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

        lines = ["可用工具："]
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
    memory_store=None,
    runtime_services: RuntimeServices | None = None,
    enable_shell: bool = False,
    enable_web_search: bool = True,
) -> ToolRegistry:
    """构建阶段一默认工具集。

    基础工具：
    - 文件读写
    - PROFILE 写入
    - 会话检索
    - 技能全文读取
    - M-flow 深度检索（如果可用）

    可选工具（需显式启用）：
    - Shell 执行（需审批）
    - Web 搜索
    """

    # 这里注册的 description 与 schema 字段说明会原样发给 LLM。
    # 因此业务说明统一使用中文；工具名和参数名保持英文，保证模型发起
    # function call 时仍能匹配到 Python 侧的真实 handler。
    registry = ToolRegistry()
    root = Path(workspace_dir)

    registry.register(
        ToolDefinition(
            name="file_read",
            description="读取 workspace 内的 UTF-8 文本文件。",
            schema=_schema("file_read", "读取文本文件", {"path": _string_field("相对路径")}),
            handler=partial(file_read, root),
        )
    )
    registry.register(
        ToolDefinition(
            name="file_write",
            description="在 workspace 内写入 UTF-8 文本文件。",
            schema=_schema(
                "file_write",
                "写入文本文件",
                {"path": _string_field("相对路径"), "content": _string_field("文本内容")},
            ),
            handler=partial(file_write, root),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_upsert_draft",
            description="创建或更新当前会话线程里的活跃账本草稿。",
            schema=_schema(
                "ledger_upsert_draft",
                "创建或更新一条账本草稿",
                {
                    "thread_id": _string_field("带运行时命名空间的线程 id"),
                    "source_message_id": _optional_string_field("来源消息 id"),
                    "direction": _optional_string_field("收入 income 或支出 expense"),
                    "amount_cent": _optional_integer_field("金额，单位为分"),
                    "currency": _optional_string_field("币种代码，默认 CNY"),
                    "category": _optional_string_field("分类，例如 meal 或 salary"),
                    "occurred_at": _optional_string_field("发生时间，ISO 8601 格式"),
                    "merchant": _optional_string_field("商家或交易对方"),
                    "note": _optional_string_field("自由备注"),
                    "missing_fields": _array_field("仍缺失的必要字段列表"),
                },
                required=["thread_id"],
            ),
            handler=partial(ledger_upsert_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_get_active_draft",
            description="读取当前会话线程里的活跃账本草稿。",
            schema=_schema(
                "ledger_get_active_draft",
                "读取一条账本草稿",
                {"thread_id": _string_field("带运行时命名空间的线程 id")},
            ),
            handler=partial(ledger_get_active_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_commit_draft",
            description="把字段完整的账本草稿提交为正式账目。",
            schema=_schema(
                "ledger_commit_draft",
                "提交一条账本草稿",
                {"thread_id": _string_field("带运行时命名空间的线程 id")},
            ),
            handler=partial(ledger_commit_draft, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_query_entries",
            description="查询已经提交的正式账目。",
            schema=_schema(
                "ledger_query_entries",
                "查询账目",
                {
                    "direction": _optional_string_field("收入 income 或支出 expense"),
                    "category": _optional_string_field("分类过滤条件"),
                    "source_thread_id": _optional_string_field("可选的来源线程过滤条件"),
                    "occurred_from": _optional_string_field("发生时间下界，ISO 格式，包含该时间"),
                    "occurred_to": _optional_string_field("发生时间上界，ISO 格式，不包含该时间"),
                    "limit": _integer_field("结果数量上限"),
                },
                required=["limit"],
            ),
            handler=partial(ledger_query_entries, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="ledger_summary",
            description="汇总已经提交的正式账目。",
            schema=_schema(
                "ledger_summary",
                "汇总账目",
                {
                    "category": _optional_string_field("分类过滤条件"),
                    "source_thread_id": _optional_string_field("可选的来源线程过滤条件"),
                    "occurred_from": _optional_string_field("发生时间下界，ISO 格式，包含该时间"),
                    "occurred_to": _optional_string_field("发生时间上界，ISO 格式，不包含该时间"),
                },
                required=[],
            ),
            handler=partial(ledger_summary, ledger_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="profile_write",
            description="替换 `PROFILE.md` 内容，并在下一轮对话生效。",
            schema=_schema("profile_write", "替换 PROFILE.md", {"content": _string_field("用户档案内容")}),
            handler=partial(profile_write, always_on_memory),
        )
    )
    registry.register(
        ToolDefinition(
            name="memory_search",
            description="搜索可审计的长期记忆。",
            schema=_schema(
                "memory_search",
                "搜索长期记忆",
                {"query": _string_field("搜索关键词"), "limit": _integer_field("结果数量上限")},
            ),
            handler=partial(memory_search, memory_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="memory_list_recent",
            description="列出最近的可审计长期记忆。",
            schema=_schema(
                "memory_list_recent",
                "列出最近长期记忆",
                {"limit": _integer_field("结果数量上限")},
            ),
            handler=partial(memory_list_recent, memory_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="memory_forget",
            description="将一条长期记忆标记为过时。",
            schema=_schema(
                "memory_forget",
                "遗忘一条长期记忆",
                {"memory_id": _string_field("记忆 id")},
            ),
            handler=partial(memory_forget, memory_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="search_sessions",
            description="在 SQLite 会话归档中搜索历史对话内容。",
            schema=_schema(
                "search_sessions",
                "搜索会话归档",
                {"query": _string_field("搜索关键词"), "limit": _integer_field("结果数量上限")},
            ),
            handler=partial(search_sessions, session_archive),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_add",
            description="创建一条长期笔记。",
            schema=_schema(
                "note_add",
                "创建笔记",
                {
                    "note_type": _string_field("笔记类型，例如 preference 或 plan"),
                    "title": _string_field("简短标题"),
                    "content": _string_field("完整笔记内容"),
                    "importance": _string_field("重要性：low、medium 或 high"),
                    "is_user_explicit": _boolean_field("用户是否明确要求保存"),
                    "source_message_id": _optional_string_field("来源消息 id"),
                    "source_thread_id": _optional_string_field("来源线程 id"),
                },
                required=["note_type", "title", "content", "importance", "is_user_explicit"],
            ),
            handler=partial(note_add, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_search",
            description="搜索已保存的长期笔记。",
            schema=_schema(
                "note_search",
                "搜索笔记",
                {
                    "query": _string_field("搜索关键词"),
                    "limit": _integer_field("结果数量上限"),
                },
            ),
            handler=partial(note_search, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_list_recent",
            description="列出最近更新的笔记。",
            schema=_schema(
                "note_list_recent",
                "列出最近笔记",
                {"limit": _integer_field("结果数量上限")},
            ),
            handler=partial(note_list_recent, note_store),
        )
    )
    registry.register(
        ToolDefinition(
            name="note_update",
            description="原地更新一条已有笔记。",
            schema=_schema(
                "note_update",
                "更新笔记",
                {
                    "note_id": _string_field("已有笔记 id"),
                    "title": _string_field("更新后的标题"),
                    "content": _string_field("更新后的内容"),
                    "importance": _string_field("更新后的重要性"),
                },
            ),
            handler=partial(note_update, note_store),
        )
    )

    registry.register(
        ToolDefinition(
            name="read_skill",
            description="按名称读取一个 skill 的完整内容。",
            schema=_schema("read_skill", "读取一个 skill", {"skill_name": _string_field("skill 名称")}),
            handler=partial(read_skill, skill_loader),
        )
    )

    if runtime_services is not None:
        registry.register(
            ToolDefinition(
                name="cron_create_task",
                description="创建一个立即生效并持久化到 `CRON.yaml` 的 cron 任务。",
                schema=_schema(
                    "cron_create_task",
                    "创建热生效 cron 任务",
                    {
                        "name": _string_field("任务名称"),
                        "schedule": _string_field("cron 表达式，例如 0 9 * * *"),
                        "prompt": _string_field("任务触发时要执行的提示词"),
                        "timezone": _string_field("IANA 时区，例如 Asia/Shanghai"),
                        "description": _optional_string_field("任务描述"),
                        "output_channel": _optional_string_field("输出渠道，默认当前渠道"),
                        "output_session_id": _optional_string_field("输出会话 id，默认当前会话"),
                        "enabled": _boolean_field("任务是否启用"),
                    },
                    required=["name", "schedule", "prompt", "timezone", "enabled"],
                ),
                handler=partial(cron_create_task, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="cron_update_task",
                description="更新一个热生效 cron 任务，并持久化到 `CRON.yaml`。",
                schema=_schema(
                    "cron_update_task",
                    "更新热生效 cron 任务",
                    {
                        "task_id": _string_field("已有任务 id"),
                        "name": _string_field("任务名称"),
                        "schedule": _string_field("cron 表达式"),
                        "prompt": _string_field("任务触发时要执行的提示词"),
                        "timezone": _string_field("IANA 时区"),
                        "description": _optional_string_field("任务描述"),
                        "output_channel": _optional_string_field("输出渠道"),
                        "output_session_id": _optional_string_field("输出会话 id"),
                        "enabled": _boolean_field("任务是否启用"),
                    },
                    required=["task_id", "name", "schedule", "prompt", "timezone", "enabled"],
                ),
                handler=partial(cron_update_task, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="cron_list_tasks",
                description="列出热生效 cron 任务，包括下次运行时间和最近运行 id。",
                schema=_schema("cron_list_tasks", "列出热生效 cron 任务", {}, required=[]),
                handler=partial(cron_list_tasks, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="cron_delete_task",
                description="删除一个热生效 cron 任务，并持久化 `CRON.yaml`。",
                schema=_schema(
                    "cron_delete_task",
                    "删除热生效 cron 任务",
                    {"task_id": _string_field("任务 id")},
                ),
                handler=partial(cron_delete_task, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="cron_run_now",
                description="立即触发一个 cron 任务，并返回本次执行生成的 run id。",
                schema=_schema(
                    "cron_run_now",
                    "立即运行 cron 任务",
                    {"task_id": _string_field("任务 id")},
                ),
                handler=partial(cron_run_now, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="reminder_create",
                description=(
                    "创建一次性提醒。相对或绝对时间提醒都使用此工具，例如“2 分钟后”、"
                    "“今天 12:39”或“明天早上”。相对时间优先使用 `delay_seconds`。"
                ),
                schema=_reminder_create_schema(),
                handler=partial(reminder_create, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="reminder_list",
                description="列出一次性提醒，包括执行时间、状态和最近运行 id。",
                schema=_schema("reminder_list", "列出一次性提醒", {}, required=[]),
                handler=partial(reminder_list, runtime_services),
                accepts_context=True,
            )
        )
        registry.register(
            ToolDefinition(
                name="reminder_delete",
                description="删除一个一次性提醒，并持久化 `REMINDERS.yaml`。",
                schema=_schema(
                    "reminder_delete",
                    "删除一次性提醒",
                    {"reminder_id": _string_field("提醒 id")},
                ),
                handler=partial(reminder_delete, runtime_services),
                accepts_context=True,
            )
        )

    # M-flow 深度检索（可选）
    if mflow_bridge is not None and getattr(mflow_bridge, "is_available", False):
        registry.register(
            ToolDefinition(
                name="recall_memory",
                description=(
                    "使用 M-flow 图路由进行深度记忆检索。"
                    "适用于需要因果推理或跨会话关联的复杂问题。"
                    "示例：`我上周为什么决定不用 Redis？`"
                ),
                schema=_schema(
                    "recall_memory",
                    "图路由深度记忆检索",
                    {
                        "question": _string_field("要检索的问题"),
                        "top_k": _integer_field("返回的片段数量，默认 3"),
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
                    "在 workspace 目录中执行 shell 命令。"
                    "**需要审批**。用于运行脚本、系统命令等。"
                ),
                schema=_schema(
                    "shell_exec",
                    "执行 shell 命令",
                    {
                        "command": _string_field("要执行的 shell 命令"),
                        "timeout": _integer_field("超时时间，单位秒，默认 30"),
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
                    "使用 DuckDuckGo 搜索网页，返回标题、摘要和 URL。"
                ),
                schema=_schema(
                    "web_search",
                    "网页搜索",
                    {
                        "query": _string_field("搜索关键词"),
                        "num_results": _integer_field("结果数量，默认 5"),
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


def _reminder_create_schema() -> dict:
    schema = _schema(
        "reminder_create",
        "创建一次性提醒",
        {
            "title": _string_field("简短提醒标题"),
            "message": _string_field("到期时发送的提醒文本"),
            "run_at": _optional_string_field("绝对执行时间，ISO 8601 格式。使用 delay_seconds 时传 null。"),
            "delay_seconds": _optional_integer_field("相对延迟秒数，例如 300 表示五分钟后。"),
            "timezone": _string_field("用于解析无时区 run_at 的 IANA 时区，例如 Asia/Shanghai"),
            "output_channel": _optional_string_field("输出渠道，默认当前渠道"),
            "output_session_id": _optional_string_field("输出会话 id，默认当前会话"),
        },
        required=["title", "message", "timezone"],
    )
    schema["function"]["parameters"]["anyOf"] = [
        {"required": ["run_at"]},
        {"required": ["delay_seconds"]},
    ]
    return schema


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
