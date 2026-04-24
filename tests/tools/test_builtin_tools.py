"""内置工具基础行为测试。"""

import asyncio
from datetime import datetime
from pathlib import Path

from agent.tools.builtin.file_ops import file_read, file_write
from agent.tools.builtin.memory_tools import recall_memory
from agent.memory.mflow_bridge import EpisodeBundle


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
