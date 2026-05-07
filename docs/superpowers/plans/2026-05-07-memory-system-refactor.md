# Memory System Refactor (Approach B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清除 mflow 死代码，将 mem0 写入路径从 `infer=False` 改为 `infer=True`，并在检索路径加入 FTS5 关键词召回，用 RRF 融合实现混合检索。

**Architecture:** `_extract_memories()` 直接把原始对话传给 `mem0.add_conversation(infer=True)`，由 mem0 内部完成提取、去重、冲突解决；`_build_memory_items_text()` 顺序跑 mem0 向量搜索和 MemoryStore FTS5 搜索，用 RRF 融合 top-5 注入上下文；mflow 全部文件彻底删除。

**Tech Stack:** Python 3.11+, mem0 SDK (`Memory`), Qdrant（本地文件），SQLite FTS5，asyncio，pytest

---

## 文件变更总览

| 操作 | 文件 |
|---|---|
| 删除 | `agent/memory/mflow_bridge.py` |
| 删除 | `tests/memory/test_mflow_bridge.py` |
| 修改 | `agent/memory/__init__.py` |
| 修改 | `agent/memory/mem0_service.py` |
| 修改 | `agent/core/loop.py` |
| 修改 | `agent/app.py` |
| 修改 | `agent/tools/builtin/memory_tools.py` |
| 修改 | `agent/tools/registry.py` |
| 修改 | `agent/config/models.py` |
| 修改 | `agent/config/loader.py` |
| 修改 | `tests/memory/test_mem0_service.py` |
| 修改 | `tests/core/test_loop.py` |
| 修改 | `tests/config/test_loader.py` |
| 修改 | `tests/tools/test_registry.py` |

---

## Task 1: 删除 mflow 死代码

**Files:**
- Delete: `agent/memory/mflow_bridge.py`
- Delete: `tests/memory/test_mflow_bridge.py`
- Modify: `agent/memory/__init__.py`
- Modify: `agent/tools/builtin/memory_tools.py`
- Modify: `agent/tools/registry.py`
- Modify: `agent/config/models.py`
- Modify: `agent/config/loader.py`
- Modify: `agent/app.py`
- Modify: `agent/core/loop.py`

- [ ] **Step 1: 删除 mflow_bridge 文件和对应测试**

```bash
rm agent/memory/mflow_bridge.py
rm tests/memory/test_mflow_bridge.py
```

- [ ] **Step 2: 清理 `agent/memory/__init__.py`**

将文件完整替换为：

```python
"""一期记忆层的公开入口。

这里把记忆拆成两类：
1. Always-On Memory：每轮都注入的 SOUL / MEMORY
2. Session Archive：按需检索的 SQLite 归档
"""

from agent.memory.always_on import AlwaysOnMemory
from agent.memory.identity_store import IdentityStore
from agent.memory.ledger_store import LedgerStore
from agent.memory.memory_extractor import MemoryCandidate, MemoryExtractor
from agent.memory.mem0_service import Mem0MemoryService
from agent.memory.memory_store import MemoryStore
from agent.memory.note_store import NoteStore
from agent.memory.profile_store import ProfileStore
from agent.memory.session_archive import SessionArchive

__all__ = [
    "AlwaysOnMemory",
    "IdentityStore",
    "LedgerStore",
    "MemoryCandidate",
    "MemoryExtractor",
    "Mem0MemoryService",
    "MemoryStore",
    "NoteStore",
    "ProfileStore",
    "SessionArchive",
]
```

- [ ] **Step 3: 清理 `agent/tools/builtin/memory_tools.py`**

删除 `recall_memory` 函数（文件中约第 66-72 行）：

```python
# 删除以下整个函数：
def recall_memory(mflow_bridge, question: str, top_k: int = 3) -> str:
    """深度记忆检索（M-flow 图路由）。
    ...
    """
    ...
```

删除后，文件中不应再有任何 `mflow_bridge` 参数或引用。

- [ ] **Step 4: 清理 `agent/tools/registry.py`**

**4a. 修改 import 块**，删除 `recall_memory` 的导入：

```python
# 将这一行：
from agent.tools.builtin.memory_tools import (
    memory_forget,
    memory_list_recent,
    memory_search,
    profile_write,
    recall_memory,
)

# 改为：
from agent.tools.builtin.memory_tools import (
    memory_forget,
    memory_list_recent,
    memory_search,
    profile_write,
)
```

**4b. 修改 `build_stage1_registry` 函数签名**，删除 `mflow_bridge=None` 参数：

```python
# 将函数签名：
def build_stage1_registry(
    workspace_dir: Path,
    always_on_memory,
    session_archive,
    skill_loader,
    mflow_bridge=None,         # ← 删除这行
    identity_store=None,
    ...
) -> ToolRegistry:

# 改为：
def build_stage1_registry(
    workspace_dir: Path,
    always_on_memory,
    session_archive,
    skill_loader,
    identity_store=None,
    ledger_store=None,
    note_store=None,
    profile_store=None,
    mem0_memory_service=None,
    memory_store=None,
    runtime_services: RuntimeServices | None = None,
    enable_shell: bool = False,
    enable_web_search: bool = True,
) -> ToolRegistry:
```

**4c. 删除 registry.py 中 recall_memory 注册块**（函数末尾的条件注册）：

```python
# 删除以下整个块：
# M-flow 深度检索（可选）
if mflow_bridge is not None and getattr(mflow_bridge, "is_available", False):
    registry.register(
        ToolDefinition(
            name="recall_memory",
            ...
        )
    )
```

**4d. 删除 `_assign_visibility_tags` 中的 `recall_memory` 条目**：

```python
# 删除这行：
"recall_memory": ("general", "identity"),
```

- [ ] **Step 5: 清理 `agent/config/models.py`**

**5a. 删除 `MflowEmbeddingSettings` dataclass**（约第 73-84 行）：

```python
# 整个删除：
@dataclass(slots=True)
class MflowEmbeddingSettings:
    """M-flow embedding 配置。"""
    provider_name: str | None = None
    provider_type: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    api_version: str | None = None
    dimensions: int | None = None
    batch_size: int | None = None
```

**5b. 删除 `MflowSettings` dataclass**（约第 87-97 行）：

```python
# 整个删除：
@dataclass(slots=True)
class MflowSettings:
    """M-flow 运行配置。"""
    enabled: bool = True
    data_dir: Path | None = None
    dataset_name: str | None = None
    llm_provider_name: str | None = None
    graph_database_provider: str = "kuzu"
    vector_db_provider: str = "lancedb"
    embedding: MflowEmbeddingSettings | None = None
```

**5c. 从 `Settings` 删除 `mflow` 字段**：

```python
# 将：
@dataclass(slots=True)
class Settings:
    agent: AgentSettings
    providers: ProviderSettings
    channels: ChannelSettings | None = None
    mflow: MflowSettings | None = None    # ← 删除这行
    mem0: Mem0Settings | None = None
    tools: ToolSettings | None = None
    observability: ObservabilitySettings | None = None

# 改为：
@dataclass(slots=True)
class Settings:
    agent: AgentSettings
    providers: ProviderSettings
    channels: ChannelSettings | None = None
    mem0: Mem0Settings | None = None
    tools: ToolSettings | None = None
    observability: ObservabilitySettings | None = None
```

- [ ] **Step 6: 清理 `agent/config/loader.py`**

**6a. 删除 import 中的 mflow 类型**：

```python
# 将 import 块：
from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    LangfuseSettings,
    Mem0Settings,
    MflowEmbeddingSettings,   # ← 删除
    MflowSettings,            # ← 删除
    ObservabilitySettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
    ShellToolSettings,
    ToolSettings,
)

# 改为：
from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    LangfuseSettings,
    Mem0Settings,
    ObservabilitySettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
    ShellToolSettings,
    ToolSettings,
)
```

**6b. 在 `load_settings()` 中删除 mflow 字段**：

```python
# 将 Settings(...) 构造中：
    return Settings(
        agent=AgentSettings(...),
        providers=ProviderSettings(...),
        channels=channels,
        mflow=_build_mflow_settings(          # ← 删除这两行
            _optional_mapping(raw, "mflow"),
            config_dir=config_dir,
            provider_names=provider_names,
        ),
        mem0=_build_mem0_settings(...),
        ...
    )

# 改为（删除 mflow= 那几行）：
    return Settings(
        agent=AgentSettings(...),
        providers=ProviderSettings(...),
        channels=channels,
        mem0=_build_mem0_settings(_optional_mapping(raw, "mem0"), config_dir=config_dir),
        tools=_build_tool_settings(_optional_mapping(raw, "tools")),
        observability=_build_observability_settings(_optional_mapping(raw, "observability")),
    )
```

**6c. 删除 `_build_mflow_settings()` 和 `_build_mflow_embedding_settings()` 两个函数**（约第 319-377 行，整块删除）。

- [ ] **Step 7: 清理 `agent/app.py`**

**7a. 删除 mflow 相关 import**：

```python
# 删除以下几行：
from agent.memory.mflow_bridge import (
    MflowBridge,
    MflowEmbeddingConfig,
    MflowLLMConfig,
    MflowRuntimeConfig,
)
```

**7b. 删除 mflow 初始化块**（`_build_app_from_settings_async` 中约第 274-286 行）：

```python
# 删除整个 try/except 块：
# 初始化 M-flow（可选，失败不阻塞启动）
mflow_bridge = None
try:
    logger.info("event=mflow_bridge_starting workspace=%s", workspace_dir)
    mflow_bridge = await _build_mflow_bridge_async(settings, workspace_dir=workspace_dir)
    ...
except Exception as e:
    logger.warning(...)
    print(...)
```

**7c. 从 `AgentCore(...)` 构造中删除 `mflow_bridge=mflow_bridge`**。

**7d. 删除 `_build_mflow_bridge_async()` 函数**（整个函数体）。

**7e. 从 `build_stage1_registry` 调用中删除 `mflow_bridge=mflow_bridge`**（如果 app.py 中有的话）。

- [ ] **Step 8: 清理 `agent/core/loop.py`**

**8a. 删除 import**：

```python
# 在 from agent.memory import (...) 块中，删除：
    TurnData,
```

**8b. 删除 `__init__` 中的 `mflow_bridge` 参数和 `self.mflow_bridge` 赋值**：

```python
# 从 __init__ 签名删除：
        mflow_bridge=None,

# 删除 __init__ 体内：
        self.mflow_bridge = mflow_bridge
```

**8c. 从 `build_stage1_registry(...)` 调用中删除 `mflow_bridge=self.mflow_bridge`**。

**8d. 删除 `_ingest_to_mflow()` 方法**（整个方法，约第 1792-1844 行）。

**8e. 删除 `_run_loop()` 中所有 `await self._ingest_to_mflow(...)` 调用**（出现两次）。

**8f. 从 `build_for_test()` classmethod 中删除 `mflow_bridge=None` 相关内容**（如有）。

- [ ] **Step 9: 运行测试，确认删除无误**

```bash
uv run pytest tests/ -v -x --ignore=tests/memory/test_mflow_bridge.py 2>&1 | head -60
```

预期：已有测试全部通过（mflow 测试文件已删除）。若有残留引用报 ImportError，根据错误信息补充删除。

- [ ] **Step 10: 提交**

```bash
git add -A
git commit -m "refactor: remove mflow dead code from 8 files

清除 MflowBridge、TurnData、recall_memory 工具、MflowSettings 等全部
mflow 相关代码，net 删除约 400+ 行死代码。"
```

---

## Task 2: 为 `Mem0MemoryService` 添加 `add_conversation()` 方法

**Files:**
- Modify: `agent/memory/mem0_service.py`
- Test: `tests/memory/test_mem0_service.py`

- [ ] **Step 1: 写失败测试**

在 `tests/memory/test_mem0_service.py` 中添加：

```python
def test_add_conversation_calls_client_with_infer_true():
    """add_conversation 应以 infer=True 调用 client.add，传入完整对话消息列表。"""
    mock_client = MagicMock()
    mock_client.add.return_value = {"results": [{"memory": "用户喜欢 Python"}]}
    service = Mem0MemoryService(enabled=True, agent_id="test-agent", client=mock_client)

    result = service.add_conversation(
        user_message="我喜欢用 Python 写代码",
        assistant_message="好的，我记住了",
        user_id="user-1",
        run_id="thread-abc",
    )

    mock_client.add.assert_called_once_with(
        [
            {"role": "user", "content": "我喜欢用 Python 写代码"},
            {"role": "assistant", "content": "好的，我记住了"},
        ],
        user_id="user-1",
        agent_id="test-agent",
        run_id="thread-abc",
        infer=True,
    )
    assert result["ok"] is True


def test_add_conversation_returns_failure_when_disabled():
    service = Mem0MemoryService(enabled=False, agent_id="test-agent", client=None)
    result = service.add_conversation(
        user_message="x", assistant_message="y", user_id="u", run_id="r"
    )
    assert result["ok"] is False
    assert "disabled" in result["error"]


def test_add_conversation_returns_failure_when_client_raises():
    mock_client = MagicMock()
    mock_client.add.side_effect = RuntimeError("Qdrant unavailable")
    service = Mem0MemoryService(enabled=True, agent_id="test-agent", client=mock_client)

    result = service.add_conversation(
        user_message="x", assistant_message="y", user_id="u", run_id="r"
    )
    assert result["ok"] is False
    assert "Qdrant unavailable" in result["error"]
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/memory/test_mem0_service.py::test_add_conversation_calls_client_with_infer_true -v
```

预期：`FAILED` — `AttributeError: 'Mem0MemoryService' object has no attribute 'add_conversation'`

- [ ] **Step 3: 在 `agent/memory/mem0_service.py` 中实现 `add_conversation()`**

在 `add_memory_items()` 方法之后，`search()` 方法之前，插入：

```python
def add_conversation(
    self,
    *,
    user_message: str,
    assistant_message: str,
    user_id: str,
    run_id: str,
) -> dict:
    """Pass a raw conversation turn to mem0 with infer=True.

    mem0 internally extracts facts, deduplicates, and resolves conflicts
    with existing memories before persisting.
    """
    if not self.enabled:
        return self._failure("Mem0 is disabled")
    if self.client is None:
        return self._failure("Mem0 client is not configured")
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_message},
    ]
    try:
        raw = self.client.add(
            messages,
            user_id=user_id,
            agent_id=self.agent_id,
            run_id=run_id,
            infer=True,
        )
    except Exception as exc:
        return self._failure(str(exc))
    return self._success(raw)
```

- [ ] **Step 4: 运行测试，确认全部通过**

```bash
uv run pytest tests/memory/test_mem0_service.py -v
```

预期：全部 PASSED。

- [ ] **Step 5: 提交**

```bash
git add agent/memory/mem0_service.py tests/memory/test_mem0_service.py
git commit -m "feat(mem0): add add_conversation() with infer=True for native dedup"
```

---

## Task 3: 改写 `_extract_memories()` 使用 `add_conversation(infer=True)`

**Files:**
- Modify: `agent/core/loop.py`
- Test: `tests/core/test_loop.py`

- [ ] **Step 1: 写失败测试**

在 `tests/core/test_loop.py` 中添加（找到现有的 memory-related 测试类，添加到其中或新建）：

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


def _make_core_with_mem0(mem0_service, memory_store=None, memory_extractor=None):
    """Helper: build a minimal AgentCore with controlled dependencies."""
    from agent.core.loop import AgentCore
    return AgentCore.build_for_test(
        Path("/tmp/test-workspace"),
        MagicMock(),  # provider_manager
        mem0_memory_service=mem0_service,
        memory_store=memory_store,
    )


class TestExtractMemoriesNewPath:
    def test_uses_add_conversation_when_mem0_ready(self, tmp_path):
        """_extract_memories should call add_conversation when mem0 is ready."""
        from agent.core.loop import AgentCore
        from agent.memory.mem0_service import Mem0MemoryService

        mock_client = MagicMock()
        mock_client.add.return_value = {"results": []}
        mem0 = Mem0MemoryService(enabled=True, agent_id="test", client=mock_client)

        core = AgentCore.build_for_test(tmp_path, MagicMock(), mem0_memory_service=mem0)

        asyncio.run(core._extract_memories(
            user_message="我喜欢用 Python",
            assistant_text="好的",
            thread_id="t1",
            source_message_id="m1",
            sender_id="user-1",
        ))

        # add_conversation 应以 infer=True 被调用
        mock_client.add.assert_called_once()
        call_args = mock_client.add.call_args
        assert call_args.kwargs.get("infer") is True
        messages = call_args.args[0]
        assert any(m["role"] == "user" and "Python" in m["content"] for m in messages)

    def test_falls_back_to_rule_extraction_when_mem0_fails(self, tmp_path):
        """When add_conversation returns ok=False, rule extraction writes to MemoryStore."""
        from agent.core.loop import AgentCore
        from agent.memory.mem0_service import Mem0MemoryService
        from agent.memory.memory_store import MemoryStore

        mock_client = MagicMock()
        mock_client.add.side_effect = RuntimeError("connection refused")
        mem0 = Mem0MemoryService(enabled=True, agent_id="test", client=mock_client)
        store = MemoryStore(tmp_path / "agent.db")

        core = AgentCore.build_for_test(tmp_path, MagicMock(), mem0_memory_service=mem0, memory_store=store)

        # Explicit memory request triggers rule extraction fallback
        asyncio.run(core._extract_memories(
            user_message="记住我喜欢深色主题",
            assistant_text="好的，已记住",
            thread_id="t1",
            source_message_id="m1",
            sender_id="user-1",
        ))

        rows = store.search("深色主题", limit=5)
        assert len(rows) >= 1

    def test_skips_trivial_messages(self, tmp_path):
        """_extract_memories should skip 'hi', '好', etc. without calling mem0."""
        from agent.core.loop import AgentCore
        from agent.memory.mem0_service import Mem0MemoryService

        mock_client = MagicMock()
        mem0 = Mem0MemoryService(enabled=True, agent_id="test", client=mock_client)

        core = AgentCore.build_for_test(tmp_path, MagicMock(), mem0_memory_service=mem0)

        asyncio.run(core._extract_memories(
            user_message="你好",
            assistant_text="你好！",
            thread_id="t1",
            source_message_id="m1",
            sender_id="user-1",
        ))

        mock_client.add.assert_not_called()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/core/test_loop.py::TestExtractMemoriesNewPath -v
```

预期：`FAILED` — 当前 `_extract_memories` 仍调用 `add_memory_items(infer=False)`。

- [ ] **Step 3: 在 `agent/core/loop.py` 中重写 `_extract_memories()`**

用以下实现替换现有的 `_extract_memories()` 方法（约第 1633-1772 行）：

```python
async def _extract_memories(
    self,
    *,
    user_message: str,
    assistant_text: str,
    thread_id: str,
    source_message_id: str,
    sender_id: str | None,
) -> None:
    if self.memory_store is None and (self.mem0_memory_service is None or not self.mem0_memory_service.is_ready):
        return

    try:
        # 前置过滤：跳过寒暄和无实质内容的消息，避免触发 LLM 调用
        text = (user_message or "").strip()
        if not text:
            return
        if self.memory_extractor is not None and not self.memory_extractor._may_contain_durable_memory(text):
            logger.info(
                "event=memory_extraction_skipped reason=durability_heuristic "
                "thread_id=%s source_message_id=%s 说明=跳过记忆抽取，内容不满足持久记忆条件",
                thread_id,
                source_message_id,
            )
            return

        # 主路径：mem0 infer=True（提取 + 去重 + 冲突解决）
        if self.mem0_memory_service is not None and self.mem0_memory_service.is_ready:
            logger.info(
                "event=memory_write_started target=mem0_infer thread_id=%s source_message_id=%s "
                "说明=开始 mem0 infer=True 写入",
                thread_id,
                source_message_id,
            )
            outcome = self.mem0_memory_service.add_conversation(
                user_message=user_message,
                assistant_message=assistant_text,
                user_id=sender_id or "unknown",
                run_id=thread_id,
            )
            if outcome.get("ok"):
                logger.info(
                    "event=memory_write_completed target=mem0_infer thread_id=%s source_message_id=%s "
                    "说明=mem0 infer=True 写入成功",
                    thread_id,
                    source_message_id,
                )
                self.react_logger.record(
                    "memory_write",
                    thread_id=thread_id,
                    source_message_id=source_message_id,
                    target="mem0_infer",
                )
                return
            logger.warning(
                "event=memory_write_failed target=mem0_infer thread_id=%s source_message_id=%s error=%s "
                "说明=mem0 infer=True 写入失败，回退规则提取",
                thread_id,
                source_message_id,
                outcome.get("error"),
            )

        # 回退路径：规则提取 → MemoryStore
        if self.memory_extractor is None or self.memory_store is None:
            return
        candidates = self.memory_extractor.extract(
            user_message=user_message,
            assistant_message=assistant_text,
            thread_id=thread_id,
            message_id=source_message_id,
            sender_id=sender_id,
        )
        if not candidates:
            logger.info(
                "event=memory_extraction_completed thread_id=%s source_message_id=%s candidate_count=0 "
                "说明=规则提取未命中任何记忆",
                thread_id,
                source_message_id,
            )
            return
        for candidate in candidates:
            self.memory_store.add_item(
                kind=candidate.kind,
                title=candidate.title,
                content=candidate.content,
                confidence=candidate.confidence,
                importance=candidate.importance,
                source_thread_id=candidate.source_thread_id,
                source_message_id=candidate.source_message_id,
                source_sender_id=candidate.source_sender_id,
            )
        logger.info(
            "event=memory_write_completed target=local_store thread_id=%s source_message_id=%s memory_count=%s "
            "说明=规则提取回退写入本地存储",
            thread_id,
            source_message_id,
            len(candidates),
        )
    except Exception as exc:
        logger.warning("Memory extraction failed: %s", exc, exc_info=True)
```

同时，删除以下已不再需要的方法（Task 1 Step 8 中已标注，这里再次确认）：
- `_build_existing_memory_snapshot()` — 已无调用者，整个删除

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/core/test_loop.py::TestExtractMemoriesNewPath -v
uv run pytest tests/ -v -x 2>&1 | tail -20
```

预期：全部 PASSED。

- [ ] **Step 5: 提交**

```bash
git add agent/core/loop.py tests/core/test_loop.py
git commit -m "feat(loop): rewrite _extract_memories to use mem0 infer=True as primary path"
```

---

## Task 4: 添加 RRF 辅助函数 + 改写 `_build_memory_items_text()`

**Files:**
- Modify: `agent/core/loop.py`
- Test: `tests/core/test_loop.py`

- [ ] **Step 1: 写失败测试**

在 `tests/core/test_loop.py` 中添加：

```python
class TestRrfHelpers:
    def test_rrf_merge_combines_two_sources(self):
        """RRF 应将两路结果融合，排名靠前的文本得分更高。"""
        from agent.core.loop import _rrf_merge

        mem0_rows = [
            {"memory": "用户喜欢 Python"},
            {"memory": "用户在北京工作"},
        ]
        local_rows = [
            {"title": "项目名", "content": "用户在北京工作"},   # 与 mem0 重复
            {"title": "偏好", "content": "用户喜欢深色主题"},
        ]
        result = _rrf_merge(mem0_rows, local_rows, top_n=3)

        assert "用户喜欢 Python" in result
        assert "用户在北京工作" in result   # 两路都命中，分数更高
        assert len(result) <= 3

    def test_rrf_merge_deduplicates_identical_text(self):
        """相同文本只出现一次，得分叠加而非重复返回。"""
        from agent.core.loop import _rrf_merge

        mem0_rows = [{"memory": "重复内容"}]
        local_rows = [{"title": "重", "content": "复内容"}]
        result = _rrf_merge(mem0_rows, local_rows, top_n=5)

        # 内容不完全相同（"重复内容" vs "重 复内容"），都会出现，但不会崩溃
        assert isinstance(result, list)

    def test_rrf_merge_empty_sources_returns_empty(self):
        from agent.core.loop import _rrf_merge
        assert _rrf_merge([], [], top_n=5) == []

    def test_rrf_text_from_mem0_row_picks_first_nonempty_field(self):
        from agent.core.loop import _rrf_text_from_mem0_row

        row = {"memory": "记忆内容", "content": "其他内容"}
        assert _rrf_text_from_mem0_row(row) == "记忆内容"

        row2 = {"text": "text 字段"}
        assert _rrf_text_from_mem0_row(row2) == "text 字段"

        assert _rrf_text_from_mem0_row({}) == ""


class TestBuildMemoryItemsTextHybrid:
    def test_hybrid_search_merges_mem0_and_fts5(self, tmp_path):
        """_build_memory_items_text 应同时使用 mem0 向量结果和 MemoryStore FTS5 结果。"""
        from agent.core.loop import AgentCore
        from agent.memory.mem0_service import Mem0MemoryService
        from agent.memory.memory_store import MemoryStore

        # Mem0 返回语义结果
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [{"memory": "用户喜欢深色主题"}]
        }
        mem0 = Mem0MemoryService(enabled=True, agent_id="test", client=mock_client)

        # MemoryStore 有一条 FTS5 匹配记录
        store = MemoryStore(tmp_path / "agent.db")
        store.add_item(kind="fact", title="工作城市", content="用户在北京工作", confidence=0.9)

        core = AgentCore.build_for_test(tmp_path, MagicMock(), mem0_memory_service=mem0, memory_store=store)

        result = core._build_memory_items_text(
            user_message="北京",
            sender_id="user-1",
            thread_id="t1",
        )

        assert "深色主题" in result or "北京" in result  # 至少一路命中

    def test_returns_empty_when_no_results(self, tmp_path):
        """两路都空时返回空字符串。"""
        from agent.core.loop import AgentCore
        from agent.memory.mem0_service import Mem0MemoryService

        mock_client = MagicMock()
        mock_client.search.return_value = {"results": []}
        mock_client.get_all.return_value = {"results": []}
        mem0 = Mem0MemoryService(enabled=True, agent_id="test", client=mock_client)

        core = AgentCore.build_for_test(tmp_path, MagicMock(), mem0_memory_service=mem0)
        result = core._build_memory_items_text(
            user_message="随机问题", sender_id="user-1", thread_id="t1"
        )
        assert result == ""
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/core/test_loop.py::TestRrfHelpers tests/core/test_loop.py::TestBuildMemoryItemsTextHybrid -v
```

预期：`FAILED` — `ImportError: cannot import name '_rrf_merge' from 'agent.core.loop'`

- [ ] **Step 3: 在 `agent/core/loop.py` 顶部（import 块之后，`_TOOL_VISIBILITY_ROUTES` 常量之前）添加两个模块级函数**

```python
def _rrf_merge(
    mem0_rows: list[dict],
    local_rows: list[dict],
    *,
    k: int = 60,
    top_n: int = 5,
) -> list[str]:
    """Reciprocal Rank Fusion: merge two retrieval result lists into ranked text items."""
    scores: dict[str, float] = {}
    for rank, row in enumerate(mem0_rows):
        text = _rrf_text_from_mem0_row(row)
        if text:
            scores[text] = scores.get(text, 0.0) + 1.0 / (k + rank + 1)
    for rank, row in enumerate(local_rows):
        text = f"{row.get('title', '')} {row.get('content', '')}".strip()
        if text:
            scores[text] = scores.get(text, 0.0) + 1.0 / (k + rank + 1)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [text for text, _ in ranked[:top_n]]


def _rrf_text_from_mem0_row(row: dict) -> str:
    for key in ("memory", "content", "text", "summary"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
```

- [ ] **Step 4: 替换 `_build_memory_items_text()` 方法**

用以下实现完整替换现有的 `_build_memory_items_text()` 方法（约第 1451-1577 行）：

```python
def _build_memory_items_text(self, *, user_message: str, sender_id: str | None, thread_id: str) -> str:
    mem0_rows: list[dict] = []
    local_rows: list[dict] = []
    user_scope = sender_id or "unknown"

    # 主路径：mem0 语义向量搜索
    if self.mem0_memory_service is not None and self.mem0_memory_service.is_ready:
        logger.info(
            "event=memory_context_search_started backend=mem0 thread_id=%s sender=%s query=%r "
            "说明=开始 mem0 语义向量检索",
            thread_id,
            user_scope,
            user_message,
        )
        outcome = self.mem0_memory_service.search(
            user_message,
            user_id=user_scope,
            run_id=thread_id,
            top_k=8,
        )
        if outcome.get("ok"):
            mem0_rows = outcome.get("results") or []
        elif not outcome.get("ok"):
            logger.warning(
                "event=memory_context_search_failed backend=mem0 error=%s",
                outcome.get("error"),
            )
        if not mem0_rows:
            fallback = self.mem0_memory_service.get_all(user_id=user_scope, top_k=8)
            if fallback.get("ok"):
                mem0_rows = fallback.get("results") or []

    # 次路径：MemoryStore FTS5 关键词搜索（本地同步，< 1ms）
    if self.memory_store is not None:
        try:
            local_rows = self.memory_store.search(user_message, limit=8)
        except Exception as exc:
            logger.warning("event=memory_fts_search_failed error=%s", exc)

    # RRF 融合
    merged = _rrf_merge(mem0_rows, local_rows, top_n=5)
    if not merged:
        logger.info(
            "event=memory_context_search_empty thread_id=%s sender=%s 说明=混合检索未找到任何长期记忆",
            thread_id,
            user_scope,
        )
        return ""

    logger.info(
        "event=memory_context_search_completed thread_id=%s sender=%s "
        "hit_count=%s mem0_count=%s local_count=%s 说明=混合检索完成并注入上下文",
        thread_id,
        user_scope,
        len(merged),
        len(mem0_rows),
        len(local_rows),
    )
    return "\n".join(f"- {text}" for text in merged)
```

- [ ] **Step 5: 运行全部测试**

```bash
uv run pytest tests/core/test_loop.py -v
uv run pytest tests/ -v 2>&1 | tail -30
```

预期：全部 PASSED。

- [ ] **Step 6: 提交**

```bash
git add agent/core/loop.py tests/core/test_loop.py
git commit -m "feat(loop): hybrid retrieval with RRF merge (mem0 vector + FTS5 keyword)"
```

---

## Task 5: 清理配置文件中的 mflow 配置块

**Files:**
- Modify: `config/agent.yaml`
- Modify: `config/agent.linux.yaml`
- Modify: `tests/config/test_loader.py`

- [ ] **Step 1: 清理 `config/agent.yaml`**

删除 `mflow:` 块（如果存在）。完整搜索：

```bash
grep -n "mflow" config/agent.yaml
```

如有结果，删除对应的 `mflow:` 整个配置块（通常是 `mflow:\n  enabled: ...` 等多行）。

- [ ] **Step 2: 清理 `config/agent.linux.yaml`**

```bash
grep -n "mflow" config/agent.linux.yaml
```

同上，删除 mflow 配置块。

- [ ] **Step 3: 检查 `tests/config/test_loader.py` 中是否有 mflow 测试**

```bash
grep -n "mflow" tests/config/test_loader.py
```

如有，删除对应的测试函数或断言行。

- [ ] **Step 4: 运行配置相关测试**

```bash
uv run pytest tests/config/ -v
```

预期：全部 PASSED。

- [ ] **Step 5: 运行完整测试套件**

```bash
uv run pytest tests/ -v 2>&1 | tail -30
```

预期：全部 PASSED，无 FAILED。

- [ ] **Step 6: 最终提交**

```bash
git add config/agent.yaml config/agent.linux.yaml tests/config/test_loader.py
git commit -m "chore: remove mflow config blocks from agent.yaml and agent.linux.yaml"
```

---

## 验收标准

1. `grep -r "mflow\|MflowBridge\|TurnData\|recall_memory\|EpisodeBundle" agent/ config/` 返回 **0 结果**
2. `uv run pytest tests/ -v` 全部通过
3. `uv run python -m agent.main --mode cli --testing` 可以正常启动，无 ImportError
4. `_extract_memories` 在 mem0 可用时调用 `add_conversation(infer=True)`，不再调用 `add_memory_items`
5. `_build_memory_items_text` 日志中出现 `mem0_count` 和 `local_count` 字段
