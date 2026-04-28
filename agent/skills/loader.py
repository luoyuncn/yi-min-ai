"""Skill 目录扫描与全文读取。

Skill 的基本约定是：
`workspace/skills/<skill-name>/SKILL.md`

Loader 负责把这个约定变成程序可调用的接口，同时把读取范围限制在 skills 目录内。
"""

from pathlib import Path


class SkillLoader:
    """读取技能索引和技能正文。"""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)

    def get_index(self) -> str:
        """扫描 skills 目录，生成给模型看的紧凑索引。"""

        lines = ["可用技能："]
        for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
            metadata = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
            name = metadata.get("name", skill_file.parent.name)
            description = metadata.get("description", "")
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    def read_full(self, skill_name: str) -> str:
        """读取单个技能的完整内容。"""

        return self._resolve_skill_file(skill_name).read_text(encoding="utf-8")

    def _resolve_skill_file(self, skill_name: str) -> Path:
        """把技能名解析成实际文件路径，并阻止越界访问。"""

        root = self.skills_dir.resolve()
        target = (root / skill_name / "SKILL.md").resolve()
        if root not in target.parents:
            raise ValueError("Skill path resolves outside skills directory")
        return target


def _parse_frontmatter(content: str) -> dict[str, str]:
    """从最简单的 YAML front matter 里抽元数据。

    一期不额外引入 front matter 解析库，直接用最薄的规则处理即可。
    """

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata
