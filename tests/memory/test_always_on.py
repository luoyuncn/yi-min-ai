"""Always-On Memory 测试。"""

from pathlib import Path

from agent.memory.always_on import AlwaysOnMemory


def test_always_on_memory_reads_soul_and_profile(tmp_path: Path) -> None:
    """验证 SOUL.md 和 PROFILE.md 都能被正确读取。"""

    soul = tmp_path / "SOUL.md"
    profile = tmp_path / "PROFILE.md"
    soul.write_text("# Identity\nYi Min\n", encoding="utf-8")
    profile.write_text("# User Profile\n- prefers python\n", encoding="utf-8")

    store = AlwaysOnMemory(soul_file=soul, profile_file=profile)

    assert "Yi Min" in store.load_soul()
    assert "prefers python" in store.load_profile()


def test_always_on_memory_can_read_legacy_memory_when_profile_is_missing(tmp_path: Path) -> None:
    soul = tmp_path / "SOUL.md"
    memory = tmp_path / "MEMORY.md"
    soul.write_text("# Identity\nYi Min\n", encoding="utf-8")
    memory.write_text("# User Profile\n- legacy preference\n", encoding="utf-8")

    store = AlwaysOnMemory(soul_file=soul, profile_file=tmp_path / "PROFILE.md", legacy_memory_file=memory)

    assert "legacy preference" in store.load_profile()

