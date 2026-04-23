"""内置工具基础行为测试。"""

from pathlib import Path

from agent.tools.builtin.file_ops import file_read, file_write


def test_file_write_and_read_are_workspace_scoped(tmp_path: Path) -> None:
    """验证文件读写工具确实在工作区内读写文本。"""

    target = tmp_path / "notes.txt"
    file_write(tmp_path, "notes.txt", "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert file_read(tmp_path, "notes.txt") == "hello"
