"""SkillLoader 测试。"""

from pathlib import Path

import pytest

from agent.skills.loader import SkillLoader


def test_skill_loader_builds_index_and_reads_full_skill(tmp_path: Path) -> None:
    """技能索引和全文读取都应走通。"""

    skill_dir = tmp_path / "daily-briefing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-briefing\ndescription: Generate daily briefing\n---\n# Daily Briefing\n",
        encoding="utf-8",
    )

    loader = SkillLoader(tmp_path)

    index = loader.get_index()
    full = loader.read_full("daily-briefing")

    assert "daily-briefing" in index
    assert "Generate daily briefing" in index
    assert "# Daily Briefing" in full


def test_skill_loader_rejects_paths_outside_skills_directory(tmp_path: Path) -> None:
    """read_full 不能允许路径逃逸出 skills 根目录。"""

    skill_dir = tmp_path / "daily-briefing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-briefing\ndescription: Generate daily briefing\n---\n# Daily Briefing\n",
        encoding="utf-8",
    )
    outside_dir = tmp_path.parent / "outside-skill"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "SKILL.md").write_text("# Outside Skill\n", encoding="utf-8")

    loader = SkillLoader(tmp_path)

    with pytest.raises(ValueError, match="outside skills directory"):
        loader.read_full("../outside-skill")
