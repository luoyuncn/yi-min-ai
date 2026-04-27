"""Always-on persona and profile file access.

这层非常薄，目的就是保持透明：
文件就是记忆本体，人和程序都能直接读写。
"""

from pathlib import Path


class AlwaysOnMemory:
    """封装 `SOUL.md` 与高权重 `PROFILE.md` 的读取与写入。"""

    def __init__(
        self,
        soul_file: Path,
        profile_file: Path,
        legacy_memory_file: Path | None = None,
    ) -> None:
        self.soul_file = Path(soul_file)
        self.profile_file = Path(profile_file)
        self.legacy_memory_file = Path(legacy_memory_file) if legacy_memory_file is not None else None

    def load_soul(self) -> str:
        """读取人格定义。"""

        return self.soul_file.read_text(encoding="utf-8")

    def load_profile(self) -> str:
        """读取高权重用户档案。"""

        if self.profile_file.exists():
            return self.profile_file.read_text(encoding="utf-8")
        if self.legacy_memory_file is not None and self.legacy_memory_file.exists():
            return self.legacy_memory_file.read_text(encoding="utf-8")
        return ""

    def replace_profile(self, content: str) -> None:
        """整体替换 PROFILE.md 内容。

        一期先做最简单的“整文件替换”，
        后面再根据需要扩展成更细粒度的编辑操作。
        """

        self.profile_file.write_text(content, encoding="utf-8")

    def load_memory(self) -> str:
        """兼容旧调用：读取 PROFILE.md。"""

        return self.load_profile()

    def replace_memory(self, content: str) -> None:
        """兼容旧调用：写入 PROFILE.md。"""

        self.replace_profile(content)
