"""SessionArchive 测试。

重点验证两件事：
1. 归档能写入并检索
2. 重复写同一 turn 时不会把索引写坏
"""

from pathlib import Path

from agent.memory.session_archive import SessionArchive


def test_session_archive_can_write_and_search_turns(tmp_path: Path) -> None:
    """基础全文检索路径。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("cli:default", 0, "user", "请记住我喜欢 Python")
    archive.append_turn("cli:default", 1, "assistant", "收到，我会记住")

    rows = archive.search("Python", limit=5)

    assert len(rows) == 1
    assert rows[0]["role"] == "user"


def test_session_archive_can_replace_existing_turn_without_breaking_search(tmp_path: Path) -> None:
    """同一 turn 被覆盖后，FTS 检索应指向最新内容。"""

    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("cli:default", 0, "user", "第一版")
    archive.append_turn("cli:default", 0, "user", "第二版")

    rows = archive.search("第二版", limit=5)

    assert len(rows) == 1
    assert rows[0]["content"] == "第二版"
