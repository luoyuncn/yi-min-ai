"""NoteStore 测试。"""

from agent.memory.note_store import NoteStore


def test_note_store_can_add_and_search_notes(tmp_path) -> None:
    """长期笔记应支持写入与全文检索。"""

    store = NoteStore(tmp_path / "agent.db")
    note_id = store.add_note(
        note_type="preference",
        title="咖啡偏好",
        content="用户更喜欢美式，不喜欢太甜的咖啡。",
        importance="high",
        is_user_explicit=True,
        source_message_id="msg-1",
        source_thread_id="web:default:thread-1",
    )

    rows = store.search("美式", limit=5)

    assert note_id
    assert len(rows) == 1
    assert rows[0]["id"] == note_id
    assert rows[0]["note_type"] == "preference"


def test_note_store_can_update_existing_note(tmp_path) -> None:
    """笔记更新后，读取结果应反映最新内容。"""

    store = NoteStore(tmp_path / "agent.db")
    note_id = store.add_note(
        note_type="constraint",
        title="饮食限制",
        content="用户乳糖不耐受。",
        importance="high",
        is_user_explicit=True,
        source_message_id="msg-1",
        source_thread_id="web:default:thread-1",
    )

    updated = store.update_note(
        note_id,
        title="饮食限制",
        content="用户乳糖不耐受，避免推荐牛奶类饮品。",
        importance="high",
    )
    rows = store.search("牛奶", limit=5)

    assert updated is True
    assert len(rows) == 1
    assert rows[0]["id"] == note_id


def test_note_store_lists_recent_notes_first(tmp_path) -> None:
    """近期笔记列表应按更新时间倒序返回。"""

    store = NoteStore(tmp_path / "agent.db")
    first_id = store.add_note(
        note_type="plan",
        title="学习计划",
        content="下个月准备开始系统学 Rust。",
        importance="medium",
        is_user_explicit=False,
        source_message_id="msg-1",
        source_thread_id="web:default:thread-1",
    )
    second_id = store.add_note(
        note_type="preference",
        title="会议偏好",
        content="用户希望会议纪要简洁直接。",
        importance="medium",
        is_user_explicit=False,
        source_message_id="msg-2",
        source_thread_id="web:default:thread-1",
    )

    rows = store.list_recent(limit=10)

    assert [row["id"] for row in rows][:2] == [second_id, first_id]
