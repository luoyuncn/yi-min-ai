"""工作区内文件读写工具。

这类工具最重要的不是“能读写”，而是“不能越界”。
所以 `_resolve()` 会先把路径规范化，再检查它是否仍位于 workspace 内。
"""

from pathlib import Path


def file_read(workspace_dir: Path, path: str) -> str:
    """读取工作区中的 UTF-8 文本文件。"""

    return _resolve(workspace_dir, path).read_text(encoding="utf-8")


def file_write(workspace_dir: Path, path: str, content: str) -> str:
    """在工作区内写入 UTF-8 文本文件。"""

    target = _resolve(workspace_dir, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return "ok"


def _resolve(workspace_dir: Path, relative_path: str) -> Path:
    """把相对路径解析成绝对路径，并阻止逃逸出 workspace。"""

    root = Path(workspace_dir).resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Path escapes workspace")
    return target
