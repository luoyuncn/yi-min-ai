"""内置工具基础行为测试。"""

import asyncio
from datetime import datetime
from pathlib import Path

from agent.memory.mem0_service import Mem0MemoryService
from agent.memory.identity_store import IdentityStore
from agent.memory.mflow_bridge import EpisodeBundle
from agent.memory.memory_store import MemoryStore
from agent.memory.profile_store import ProfileStore
from agent.tools.builtin.file_ops import file_read, file_write
from agent.tools.builtin.identity_tools import assistant_identity_update, profile_core_update
from agent.tools.builtin.memory_tools import memory_forget, memory_list_recent, memory_search, recall_memory
from agent.tools.runtime_context import RuntimeToolContext


class FakeMem0Client:
    def __init__(self) -> None:
        self.deleted_ids: list[str] = []

    def search(self, query: str, **kwargs):
        return [{"id": "m1", "memory": "腿哥喜欢 Tims 冷萃美式。", "created_at": "2026-05-06T17:00:00+08:00"}]

    def get_all(self, **kwargs):
        return [
            {"id": "m1", "memory": "腿哥喜欢 Tims 冷萃美式。", "updated_at": "2026-05-06T17:00:00+08:00"},
            {"id": "m2", "memory": "腿哥常用中文交流。", "updated_at": "2026-05-05T17:00:00+08:00"},
        ]

    def delete(self, memory_id: str):
        self.deleted_ids.append(memory_id)
        return {"id": memory_id}


def _runtime_context(tmp_path: Path) -> RuntimeToolContext:
    return RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="msg-1",
        channel="feishu",
        channel_instance="feishu",
        session_id="session-1",
        thread_key="feishu:feishu:chat-1",
        sender="ou_123",
        metadata={},
    )


def test_file_write_and_read_are_workspace_scoped(tmp_path: Path) -> None:
    """验证文件读写工具确实在工作区内读写文本。"""

    target = tmp_path / "notes.txt"
    file_write(tmp_path, "notes.txt", "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert file_read(tmp_path, "notes.txt") == "hello"


def test_recall_memory_does_not_use_run_coroutine_threadsafe_on_current_loop(monkeypatch) -> None:
    """在运行中的事件循环内，recall_memory 不应走会死锁的同线程 future 分支。"""

    async def query(question: str, top_k: int):
        return [
            EpisodeBundle(
                episode_id="ep-1",
                summary="使用阿里云 embedding。",
                facets=[],
                entities=[],
                score=0.9,
                created_at=datetime(2026, 4, 24, 14, 0, 0),
            )
        ]

    class Bridge:
        async def query(self, question: str, top_k: int):
            return await query(question, top_k)

    monkeypatch.setattr(
        "agent.tools.builtin.memory_tools.asyncio.run_coroutine_threadsafe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("run_coroutine_threadsafe should not be used on the current loop")
        ),
    )

    async def _exercise() -> str:
        return recall_memory(Bridge(), "embedding 方案", top_k=1)

    result = asyncio.run(_exercise())

    assert "Found 1 relevant episodes" in result


def test_memory_tools_search_list_and_forget_memory_items(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "agent.db")
    memory_id = store.add_item(
        kind="preference",
        title="咖啡偏好",
        content="腿哥喜欢 Tims 冷萃美式。",
        source_thread_id="thread-1",
        source_message_id="msg-1",
        source_sender_id="sender-1",
    )

    assert "咖啡偏好" in memory_search(store, query="冷萃", limit=5)
    assert "Tims 冷萃美式" in memory_list_recent(store, limit=5)
    assert memory_forget(store, memory_id=memory_id) == "ok"
    assert memory_search(store, query="冷萃", limit=5) == "No memories found."


def test_memory_tools_prefer_mem0_for_search_and_recent_list(tmp_path: Path) -> None:
    mem0_service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=FakeMem0Client())
    context = _runtime_context(tmp_path)

    search_result = memory_search(None, mem0_service, query="我喜欢喝什么", limit=5, context=context)
    recent_result = memory_list_recent(None, mem0_service, limit=5, context=context)

    assert "Tims 冷萃美式" in search_result
    assert "常用中文交流" in recent_result


def test_memory_forget_deletes_mem0_memory_when_available(tmp_path: Path) -> None:
    client = FakeMem0Client()
    mem0_service = Mem0MemoryService(enabled=True, agent_id="yi-min", client=client)

    result = memory_forget(None, mem0_service, memory_id="m1")

    assert result == "ok"
    assert client.deleted_ids == ["m1"]


def test_assistant_identity_update_writes_structured_identity_and_renders_soul(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path / "agent.db", tmp_path / "SOUL.md")

    result = assistant_identity_update(
        store,
        name="银月",
        backstory="本名玲珑，曾以器灵之身陪韩立走过漫长岁月。",
        style="冷静、利落，不说废话。",
        principles=["不编造", "不讨好"],
    )

    assert result == "ok"
    assert store.get_identity() == {
        "name": "银月",
        "backstory": "本名玲珑，曾以器灵之身陪韩立走过漫长岁月。",
        "style": "冷静、利落，不说废话。",
        "principles": ["不编造", "不讨好"],
    }
    assert "你是银月" in (tmp_path / "SOUL.md").read_text(encoding="utf-8")


def test_assistant_identity_update_can_patch_existing_soul_without_prior_db_row(tmp_path: Path) -> None:
    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text(
        "# SOUL.md\n\n"
        "## 身份\n\n"
        "你是银月。\n\n"
        "本名玲珑，曾以器灵之身陪韩立走过漫长岁月。\n\n"
        "## 风格\n\n"
        "冷静、利落，不说废话。\n\n"
        "## 原则\n\n"
        "- 不编造\n"
        "- 不讨好\n",
        encoding="utf-8",
    )
    store = IdentityStore(tmp_path / "agent.db", soul_file)

    result = assistant_identity_update(
        store,
        name="霜月",
    )

    assert result == "ok"
    assert store.get_identity()["name"] == "霜月"
    text = soul_file.read_text(encoding="utf-8")
    assert "你是霜月" in text
    assert "本名玲珑" in text


def test_profile_core_update_merges_with_existing_profile_and_renders_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "agent.db", tmp_path / "PROFILE.md")
    store.replace_profile(display_name="腿哥", core_facts=["默认中文回答"])

    result = profile_core_update(
        store,
        core_facts=["默认中文回答", "乳糖不耐受"],
    )

    assert result == "ok"
    assert store.get_profile() == {
        "display_name": "腿哥",
        "core_facts": ["默认中文回答", "乳糖不耐受"],
    }
    text = (tmp_path / "PROFILE.md").read_text(encoding="utf-8")
    assert "- 称呼：腿哥" in text
    assert "- 乳糖不耐受" in text
