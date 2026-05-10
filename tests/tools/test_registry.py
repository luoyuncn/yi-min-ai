"""ToolRegistry 测试。"""

from agent.tools.registry import build_stage1_registry


def test_stage1_registry_exposes_expected_safe_tools(tmp_path) -> None:
    """阶段一只能暴露约定好的安全工具集合。"""

    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    assert set(registry.names()) == {
        "assistant_identity_update",
        "file_read",
        "file_write",
        "fitness_profile_get",
        "fitness_profile_update",
        "fitness_settings_get",
        "fitness_settings_update",
        "fitness_workout_append",
        "fitness_workout_recent",
        "fitness_audit_recent",
        "ledger_commit_draft",
        "ledger_get_active_draft",
        "ledger_query_entries",
        "ledger_summary",
        "ledger_upsert_draft",
        "profile_core_update",
        "profile_write",
        "memory_search",
        "memory_list_recent",
        "memory_forget",
        "note_add",
        "note_list_recent",
        "note_search",
        "note_update",
        "search_sessions",
        "read_skill",
        "web_search",
        "message_send",
    }


def test_stage1_registry_can_render_tool_index(tmp_path) -> None:
    """注册表应能生成给模型阅读的工具索引。"""

    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    tool_index = registry.get_index()

    assert tool_index.startswith("可用工具：")
    assert "- assistant_identity_update:" in tool_index
    assert "- fitness_profile_get:" in tool_index
    assert "- ledger_upsert_draft:" in tool_index
    assert "- note_add:" in tool_index
    assert "- profile_core_update:" in tool_index
    assert "- web_search:" in tool_index


def test_stage1_registry_can_filter_visible_tools_by_route(tmp_path) -> None:
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    fitness_names = set(registry.names(visibility_tags={"always", "fitness"}))

    assert "read_skill" in fitness_names
    assert "fitness_profile_get" in fitness_names
    assert "fitness_workout_append" in fitness_names
    assert "ledger_summary" not in fitness_names
    assert "note_add" not in fitness_names
    assert "web_search" not in fitness_names


def test_stage1_registry_web_search_schema_supports_provider_options(tmp_path) -> None:
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    params = registry.get("web_search").schema["function"]["parameters"]

    assert params["required"] == ["query"]
    assert "allowed_domains" in params["properties"]
    assert "blocked_domains" in params["properties"]
    assert "topic" in params["properties"]
    assert "time_range" in params["properties"]


def test_stage1_registry_marks_memory_tools_as_context_aware(tmp_path) -> None:
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    assert registry.get("memory_search").accepts_context is True
    assert registry.get("memory_list_recent").accepts_context is True
    assert registry.get("memory_forget").accepts_context is True


def test_stage1_registry_exposes_cron_tools_when_scheduler_service_is_available(tmp_path) -> None:
    from agent.tools.runtime_context import RuntimeServices

    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        runtime_services=RuntimeServices(cron_scheduler=object()),
    )

    assert "cron_create_task" in registry.names()
    assert "cron_run_now" in registry.names()
    assert "cron_list_tasks" in registry.names()
    assert "cron_delete_task" in registry.names()


def test_stage1_registry_exposes_reminder_tools_when_scheduler_service_is_available(tmp_path) -> None:
    from agent.tools.runtime_context import RuntimeServices

    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        runtime_services=RuntimeServices(reminder_scheduler=object()),
    )

    assert "reminder_create" in registry.names()
    assert "reminder_list" in registry.names()
    assert "reminder_delete" in registry.names()
    params = registry.get("reminder_create").schema["function"]["parameters"]
    assert {"required": ["run_at"]} in params["anyOf"]
    assert {"required": ["delay_seconds"]} in params["anyOf"]


def test_stage1_registry_exposes_shell_when_enabled(tmp_path) -> None:
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        enable_shell=True,
    )

    assert "shell_exec" in registry.names()
