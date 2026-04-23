"""Always-On Memory 的文件读写。

这层非常薄，目的就是保持透明：
文件就是记忆本体，人和程序都能直接读写。
"""

from pathlib import Path


class AlwaysOnMemory:
    """封装 `SOUL.md` 与 `MEMORY.md` 的读取与写入。"""

    def __init__(self, soul_file: Path, memory_file: Path) -> None:
        self.soul_file = Path(soul_file)
        self.memory_file = Path(memory_file)

    def load_soul(self) -> str:
        """读取人格定义。"""

        return self.soul_file.read_text(encoding="utf-8")

    def load_memory(self) -> str:
        """读取长期事实记忆。"""

        return self.memory_file.read_text(encoding="utf-8")

    def replace_memory(self, content: str) -> None:
        """整体替换 MEMORY.md 内容。

        一期先做最简单的“整文件替换”，
        后面再根据需要扩展成更细粒度的编辑操作。
        """

        self.memory_file.write_text(content, encoding="utf-8")
