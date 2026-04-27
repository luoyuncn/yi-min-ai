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
        "file_read",
        "file_write",
        "ledger_commit_draft",
        "ledger_get_active_draft",
        "ledger_query_entries",
        "ledger_summary",
        "ledger_upsert_draft",
        "memory_write",
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

    assert tool_index.startswith("Available Tools:")
    assert "- ledger_upsert_draft:" in tool_index
    assert "- note_add:" in tool_index
    assert "- web_search:" in tool_index


def test_stage1_registry_only_registers_recall_memory_when_mflow_is_available(tmp_path) -> None:
    """recall_memory 只应在 M-flow 已可用时暴露给模型。"""

    unavailable_registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        mflow_bridge=type("Bridge", (), {"is_available": False})(),
    )
    available_registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        mflow_bridge=type("Bridge", (), {"is_available": True})(),
    )

    assert "recall_memory" not in unavailable_registry.names()
    assert "recall_memory" in available_registry.names()
