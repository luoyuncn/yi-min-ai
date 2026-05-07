# Memory System Refactor — Design Spec

**Date:** 2026-05-07  
**Scope:** 方案 B — 清除 mflow 死代码 + mem0 infer=True + 混合检索  
**Status:** 待实现

---

## 背景与动机

现有记忆系统存在三个核心问题：

1. **写入路径产生脏数据**：使用 `infer=False` 绕过了 mem0 自带的去重和冲突解决。同一事实每次对话后都会重复写入，向量库随时间积累大量冗余和矛盾条目，检索时信噪比持续下降。

2. **检索路径是单腿的**：只用 Qdrant 向量语义搜索。对人名、项目名、技术术语等专有名词，向量相似度天然偏弱，而 `MemoryStore` 里已建好的 FTS5 全文索引从未接入主路径。

3. **mflow 已弃用但死代码散落 8 个文件**：`MflowBridge`、`TurnData`、`recall_memory` 工具、配置模型等仍占据代码库，增加理解和维护成本。

---

## 目标

- 写入：mem0 自动去重 + 冲突解决，停止积累脏记忆
- 检索：向量 + 关键词混合，专有名词召回率提升
- 代码：移除 mflow 死代码，净减少代码量

## 非目标（本次不做）

- 记忆衰减打分（decay）
- Consolidation 定时任务
- Context offloading（大型工具结果压缩）
- 程序性记忆（技能库动态生成）

---

## 架构变化

### 写入路径（重构前）

```
reply 发出
  → _schedule_memory_extraction()
  → asyncio.create_task(_extract_memories())
      → MemoryExtractor.extract_async()          ← 自定义 LLM 提取
          → _may_contain_durable_memory() 预过滤
          → LLM call → MemoryCandidate[]
          → confidence >= 0.65 过滤
      → mem0_service.add_memory_items(items, infer=False)   ← 无去重，无冲突解决
          → 每条 item 单独 client.add(content, infer=False)
```

### 写入路径（重构后）

```
reply 发出
  → _schedule_memory_extraction()
  → asyncio.create_task(_extract_memories())
      if mem0.is_ready:
        → mem0_service.add_conversation(user_msg, assistant_msg)
            → client.add([{role:user,...},{role:assistant,...}], infer=True)
            → mem0 内部：LLM 提取事实 → 搜索已有记忆 → 去重/合并/标记冲突废弃
        if 失败:
        → fallback: MemoryExtractor.extract() [规则] + MemoryStore.add_item()
      else:
        → MemoryExtractor.extract() [规则] + MemoryStore.add_item()
```

**关键变化**：`infer=True` 时 mem0 内部做三件事：
1. 调用配置的 LLM 从对话中提取事实
2. 搜索已有记忆，检测冲突（如"用户用 Java 8" vs "用户升级到 Java 21"）
3. 自动 ADD / UPDATE / DELETE，旧事实标记废弃

### 检索路径（重构前）

```
用户消息
  → mem0.search(query, top_k=5)
      if 空: fallback mem0.get_all(top_k=5)
  → format → 注入 [检索到的长期记忆]
```

### 检索路径（重构后）

```
用户消息
  → 顺序执行（两步都极快，总耗时取决于 mem0 向量搜索）：
      A: mem0.search(query, top_k=8)              ← 语义向量，约几十 ms
      B: MemoryStore.search(query, limit=8)        ← FTS5 关键词，< 1 ms
  → RRF 融合 (k=60):
      score(text) = Σ 1/(60 + rank_in_source + 1)
  → 文本去重（相同内容只保留一条）
  → top 5 → format → 注入 [检索到的长期记忆]
```

注：MemoryStore.search() 是同步调用，不需要 asyncio。先跑 mem0（有网络/IO 延迟），再跑 FTS5（内存操作），总延迟 ≈ mem0 单次搜索延迟，不增加额外等待。

**为什么用 RRF 而非线性加权**：RRF 无需标注数据调参，对冷启动友好；基于排名而非原始分值，天然归一化了向量余弦分和 FTS5 BM25 分的量纲差异。

---

## 受影响文件清单

### 删除
| 文件 | 原因 |
|---|---|
| `agent/memory/mflow_bridge.py` | mflow 已弃用，整文件删除 |

### 修改

**`agent/memory/mem0_service.py`**  
新增 `add_conversation(user_message, assistant_message, *, user_id, run_id)` 方法：
- 调用 `client.add([user_msg_dict, assistant_msg_dict], infer=True, ...)`
- 返回统一的 `{ok, results, error}` 格式

**`agent/core/loop.py`**  
- `_extract_memories()`：主路径改为 `mem0_service.add_conversation(infer=True)`，失败（抛异常 或 `ok=False`）时 fallback 到规则提取 + MemoryStore。写入前保留 `_may_contain_durable_memory()` 预过滤，避免对"你好"/"好的"等无实质内容的消息触发 mem0 LLM 调用。
- `_build_memory_items_text()`：先跑 mem0 search，再跑 MemoryStore FTS5，然后 `_rrf_merge()` 融合（helper 定义在同文件）
- 删除 `_ingest_to_mflow()`、`TurnData` 使用、所有 mflow 相关 import 和调用

**`agent/memory/__init__.py`**  
- 删除 `MflowBridge`、`TurnData` 导出

**`agent/app.py`**  
- 删除 `_build_mflow_bridge_async()`、mflow 初始化代码和相关 import
- 删除 `AgentCore` 构造中的 `mflow_bridge=mflow_bridge` 参数

**`agent/tools/builtin/memory_tools.py`**  
- 删除 `recall_memory(mflow_bridge, ...)` 函数

**`agent/tools/registry.py`**  
- 删除 `recall_memory` 工具注册

**`agent/config/models.py`**  
- 删除 `MflowEmbeddingSettings`、`MflowLLMSettings`、`MflowSettings`、`MflowRuntimeSettings` dataclass

**`agent/config/loader.py`**  
- 删除 mflow 相关配置解析逻辑

---

## 关键设计决策

### 1. 为什么主路径用 `infer=True` 而不是继续用 `infer=False`

`infer=False` 只是把内容写进向量库，不做任何语义处理。相同事实每轮都写，冲突事实并存，向量库质量随时间持续退化。`infer=True` 让 mem0 在每次写入时检查并修正历史记忆，这是 mem0 最核心的设计价值，之前完全没用到。

### 2. 为什么保留 MemoryExtractor 作为 fallback

- mem0 不可用时（API 密钥未配置、网络问题）系统仍能工作
- MemoryStore SQLite 提供本地可查的审计日志
- `memory_search` / `memory_list_recent` 等工具依赖 MemoryStore

### 3. 为什么检索并行而非串行

FTS5 是本地同步调用，耗时 < 1ms；mem0 search 是本地 Qdrant 向量召回，耗时约几十毫秒。并行跑两个不会增加总延迟，只取决于较慢的那个（mem0），结果合并是纯内存操作。

### 4. MemoryStore 的角色边界

重构后 MemoryStore 不再是"主存储的一部分"，而是：
- **备份写入**：当 mem0 失败时接收规则提取的结果
- **工具后端**：为 `memory_search` / `memory_list_recent` 提供可查询的本地记忆
- **混合检索来源之一**：提供 FTS5 关键词召回

这个边界清晰，不需要删除 MemoryStore。

---

## RRF 融合算法

位置：`agent/core/loop.py` 模块级私有函数，供 `_build_memory_items_text()` 调用。

```python
def _rrf_merge(
    mem0_rows: list[dict],
    local_rows: list[dict],
    *,
    k: int = 60,
    top_n: int = 5,
) -> list[str]:
    """Reciprocal Rank Fusion：合并两路召回结果，返回最终文本列表。"""
    scores: dict[str, float] = {}

    for rank, row in enumerate(mem0_rows):
        # mem0 原生结果，字段名为 memory/content/text/summary
        text = _rrf_text_from_mem0_row(row)
        if text:
            scores[text] = scores.get(text, 0.0) + 1.0 / (k + rank + 1)

    for rank, row in enumerate(local_rows):
        # MemoryStore 结果，字段为 title + content
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

---

## 预期效果

| 指标 | 重构前 | 重构后 |
|---|---|---|
| 重复记忆 | 每轮累积 | mem0 自动去重 |
| 冲突记忆 | 并存，检索时同时出现 | 旧条目自动废弃 |
| 专有名词召回 | 依赖语义相似度，弱 | FTS5 精确匹配兜底 |
| 代码行数 | mflow 约 400 行死代码 | 净减少 |
| `_extract_memories` 复杂度 | MemoryExtractor LLM → 校验 → 逐条写入 | 一次 add_conversation 调用 |

---

## 风险与注意事项

1. **`infer=True` 会调用额外 LLM**：每次回复后异步触发，不阻塞响应，但会多消耗 token。如 token 成本敏感，可加前置过滤（保留现有 `_may_contain_durable_memory` 逻辑）。

2. **mem0 内部提取可能丢失 `kind` 元数据**：`infer=True` 写入的记忆没有 `profile/preference/fact` 等分类标签，`_format_mem0_row` 已使用 `memory` 字段而非 `kind`，工具显示不受影响，但记忆类型信息会丢失。可接受。

3. **mflow config 字段清理**：`agent.yaml` 和 `agent.linux.yaml` 中如有 `mflow:` 配置块需同步删除，否则 loader 解析会报警告（取决于 loader 是否严格）。

4. **首次切换后 Qdrant 集合里已有 `infer=False` 写入的旧记忆**：这些记忆格式正常，mem0 后续 `infer=True` 写入时会检测并处理冲突，不需要手动清理。
