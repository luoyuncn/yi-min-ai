"""Always-On Memory 测试。"""

from pathlib import Path

from agent.memory.always_on import AlwaysOnMemory


def test_always_on_memory_reads_soul_and_memory(tmp_path: Path) -> None:
    """验证 SOUL.md 和 MEMORY.md 都能被正确读取。"""

    soul = tmp_path / "SOUL.md"
    memory = tmp_path / "MEMORY.md"
    soul.write_text("# Identity\nYi Min\n", encoding="utf-8")
    memory.write_text("# User Profile\n- prefers python\n", encoding="utf-8")

    store = AlwaysOnMemory(soul_file=soul, memory_file=memory)

    assert "Yi Min" in store.load_soul()
    assert "prefers python" in store.load_memory()

