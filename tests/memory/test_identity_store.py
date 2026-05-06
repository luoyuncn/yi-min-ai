from pathlib import Path

from agent.memory.identity_store import IdentityStore
from agent.memory.profile_store import ProfileStore


def test_identity_store_renders_soul_file_from_structured_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    soul_file = tmp_path / "SOUL.md"

    store = IdentityStore(db_path, soul_file)
    store.replace_identity(
        name="银月",
        backstory="本名玲珑，曾以器灵之身行走人间。",
        style="冷静、利落，不说空话。",
        principles=["不编造", "不讨好"],
    )

    text = soul_file.read_text(encoding="utf-8")
    assert "你是银月" in text
    assert "本名玲珑" in text
    assert "冷静、利落" in text
    assert "- 不编造" in text
    assert "- 不讨好" in text


def test_identity_store_persists_and_returns_latest_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    soul_file = tmp_path / "SOUL.md"

    IdentityStore(db_path, soul_file).replace_identity(
        name="银月",
        backstory="旧设定",
        style="冷静",
        principles=["不编造"],
    )

    reloaded = IdentityStore(db_path, soul_file)
    current = reloaded.get_identity()

    assert current["name"] == "银月"
    assert current["backstory"] == "旧设定"
    assert current["style"] == "冷静"
    assert current["principles"] == ["不编造"]


def test_profile_store_renders_profile_file_from_structured_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    profile_file = tmp_path / "PROFILE.md"

    store = ProfileStore(db_path, profile_file)
    store.replace_profile(
        display_name="腿哥",
        core_facts=["默认中文回答", "乳糖不耐受"],
    )

    text = profile_file.read_text(encoding="utf-8")
    assert "# User Profile" in text
    assert "- 称呼：腿哥" in text
    assert "- 默认中文回答" in text
    assert "- 乳糖不耐受" in text


def test_profile_store_persists_and_returns_latest_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    profile_file = tmp_path / "PROFILE.md"

    ProfileStore(db_path, profile_file).replace_profile(
        display_name="腿哥",
        core_facts=["只保留核心资料"],
    )

    reloaded = ProfileStore(db_path, profile_file)
    current = reloaded.get_profile()

    assert current["display_name"] == "腿哥"
    assert current["core_facts"] == ["只保留核心资料"]
