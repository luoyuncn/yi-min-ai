"""Verify that visibility_tags=None returns all tools (the invariant proactive routing depends on)."""
from agent.tools.registry import ToolRegistry
from agent.tools.models import ToolDefinition


def test_none_visibility_tags_returns_all_tools() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="tool_a", description="A", schema={},
        handler=lambda: None, visibility_tags=("fitness",)
    ))
    registry.register(ToolDefinition(
        name="tool_b", description="B", schema={},
        handler=lambda: None, visibility_tags=("general",)
    ))

    all_schemas = registry.get_schemas(visibility_tags=None)
    assert len(all_schemas) == 2

    fitness_only = registry.get_schemas(visibility_tags={"fitness"})
    assert len(fitness_only) == 1
