from pathlib import Path

from agent.memory.memory_store import MemoryStore


def test_memory_store_adds_searches_and_lists_active_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent.db")

    memory_id = store.add_item(
        kind="preference",
        title="咖啡偏好",
        content="腿哥喜欢 Tims 冷萃美式。",
        importance="medium",
        confidence=0.9,
        source_thread_id="feishu:feishu:chat-1",
        source_message_id="msg-1",
        source_sender_id="ou-user-1",
    )

    rows = store.search("冷萃", limit=5)
    recent = store.list_recent(limit=5)

    assert rows[0]["id"] == memory_id
    assert rows[0]["source_sender_id"] == "ou-user-1"
    assert recent[0]["title"] == "咖啡偏好"


def test_memory_store_can_mark_items_obsolete(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent.db")
    memory_id = store.add_item(
        kind="profile",
        title="称呼",
        content="用户称呼是腿哥。",
        source_thread_id="thread-1",
        source_message_id="msg-1",
        source_sender_id="sender-1",
    )

    assert store.mark_obsolete(memory_id) is True

    assert store.search("腿哥", limit=5) == []
    assert store.list_recent(limit=5) == []


def test_memory_store_replaces_item_with_supersession(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent.db")
    old_id = store.add_item(
        kind="profile",
        title="称呼",
        content="用户称呼是小王。",
        source_thread_id="thread-1",
        source_message_id="msg-1",
        source_sender_id="sender-1",
    )

    new_id = store.replace_item(
        old_id,
        kind="profile",
        title="称呼",
        content="用户称呼是腿哥。",
        source_thread_id="thread-1",
        source_message_id="msg-2",
        source_sender_id="sender-1",
    )

    rows = store.search("腿哥", limit=5)

    assert new_id
    assert rows[0]["id"] == new_id
    assert rows[0]["supersedes_id"] == old_id
    assert store.search("小王", limit=5) == []
