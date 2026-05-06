# 记忆整顿 + 健身修复 + 提示词重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复健身初始化确认守卫误触发问题，重构系统提示词使 SOUL 身份前置无标签，整顿记忆系统跨渠道漂移和双后端混乱。

**Architecture:** 三个独立变更按 P0→P1→P2 顺序实施，每个任务产出可独立测试的变更。健身修复最小化（8 行）；提示词重构修改 context.py 的拼接顺序和 app.py 的规则文本；记忆整顿增加 default_user_id 配置并贯穿 config→app→loop。

**Tech Stack:** Python 3.12, pytest, dataclasses (slots=True), pathlib

---

## 文件改动总览

| 文件 | 变更类型 | 原因 |
|------|----------|------|
| `agent/tools/builtin/fitness_tools.py` | Modify | 初始化场景跳过确认守卫 |
| `tests/tools/test_fitness_tools.py` | Modify | 新增初始化场景测试 |
| `agent/core/context.py` | Modify | 重排 system prompt 顺序，移除冗余块 |
| `agent/app.py` | Modify | 精简 `_build_system_prompt()`，移除启动时间行 |
| `tests/core/test_context.py` | Modify | 更新断言以匹配新结构 |
| `agent/config/models.py` | Modify | `AgentSettings` 增加 `default_user_id` |
| `agent/config/loader.py` | Modify | 解析 `default_user_id` 配置项 |
| `agent/core/loop.py` | Modify | `AgentCore` 存储和使用 `default_user_id`，mem0 后端互斥 |
| `agent/app.py` | Modify | 传递 `default_user_id` 给 `AgentCore` |
| `tests/config/test_loader.py` | Modify / read | 确认配置解析兼容 |

---

## Task 1：健身初始化修复（P0）

**Files:**
- Modify: `agent/tools/builtin/fitness_tools.py`
- Modify: `tests/tools/test_fitness_tools.py`

- [ ] **Step 1：写失败测试**

在 `tests/tools/test_fitness_tools.py` 末尾追加：

```python
def test_fitness_profile_update_skips_guard_when_profile_is_empty(tmp_path: Path) -> None:
    """首次初始化时档案为空，major fields 应直接写入，不触发确认守卫。"""
    store = FitnessFileStore(tmp_path)
    pending = FitnessPendingChangeStore()
    services = RuntimeServices(fitness_change_store=pending)
    context = RuntimeToolContext(
        workspace_dir=tmp_path,
        run_id="run-init",
        channel="feishu",
        channel_instance="feishu",
        session_id="chat-1",
        thread_key="feishu:feishu:chat-1",
        sender="user-1",
        metadata={"runtime_services": services},
    )

    result = fitness_profile_update(
        store,
        goal="增肌减脂",
        level="新手",
        equipment="哑铃、弹力带",
        context=context,
    )

    # 应直接写入，不返回"需要确认"
    assert "需要确认" not in result
    assert "Updated fitness profile" in result
    # 档案里应已有数据
    profile_text = fitness_profile_get(store)
    assert "增肌减脂" in profile_text
    # 不应有 pending change
    assert pending.get("feishu:feishu:chat-1", sender="user-1") is None
```

- [ ] **Step 2：运行确认测试当前失败**

```bash
cd D:\dev\agent\yi-min-ai
python -m pytest tests/tools/test_fitness_tools.py::test_fitness_profile_update_skips_guard_when_profile_is_empty -v
```

预期：FAIL，`assert "需要确认" not in result` 失败

- [ ] **Step 3：在 fitness_tools.py 添加空档案检测函数**

在 `agent/tools/builtin/fitness_tools.py` 底部（`_summarize_changes` 之后）追加：

```python
def _is_profile_empty(profile: dict) -> bool:
    """检查健身档案是否为空（未初始化），用于跳过首次写入的确认守卫。"""
    training = profile.get("training_profile") or {}
    return not any(training.get(key) for key in ("goal", "level", "equipment"))
```

- [ ] **Step 4：修改 fitness_profile_update 加入空档案豁免**

找到 `agent/tools/builtin/fitness_tools.py` 中 `fitness_profile_update` 里以下代码段：

```python
    if _requires_confirmation(staged_updates, _major_profile_fields()) and context is not None:
        services = (context.metadata or {}).get("runtime_services")
        change_store = getattr(services, "fitness_change_store", None) if services is not None else None
        if change_store is not None:
```

替换为：

```python
    if _requires_confirmation(staged_updates, _major_profile_fields()) and context is not None:
        current_profile = store.get_profile()
        if _is_profile_empty(current_profile):
            pass  # 首次初始化，跳过确认守卫直接写入
        else:
            services = (context.metadata or {}).get("runtime_services")
            change_store = getattr(services, "fitness_change_store", None) if services is not None else None
            if change_store is not None:
```

同时在该 `if change_store is not None:` 块的末尾（`return (...)` 后面）补上对应的缩进闭合，确保 `else` 分支只包裹原来的守卫逻辑。完整修改后的段落如下（从 `if _requires_confirmation` 到结束）：

```python
    if _requires_confirmation(staged_updates, _major_profile_fields()) and context is not None:
        current_profile = store.get_profile()
        if not _is_profile_empty(current_profile):
            services = (context.metadata or {}).get("runtime_services")
            change_store = getattr(services, "fitness_change_store", None) if services is not None else None
            if change_store is not None:
                summary = _summarize_changes(staged_updates, prefix="健身档案")
                change_store.stage(
                    context.thread_key,
                    sender=context.sender,
                    target="profile",
                    updates=staged_updates,
                    summary=summary,
                )
                return (
                    f"这是需要确认的重大健身档案变更，我已暂存：{summary}。\n"
                    "确认写入请回复"确认"，放弃本次修改请回复"取消"。"
                )
```

- [ ] **Step 5：运行新测试确认通过**

```bash
python -m pytest tests/tools/test_fitness_tools.py::test_fitness_profile_update_skips_guard_when_profile_is_empty -v
```

预期：PASS

- [ ] **Step 6：运行全部健身工具测试确认无退化**

```bash
python -m pytest tests/tools/test_fitness_tools.py -v
```

预期：所有 5 个测试全部 PASS（包括原有确认守卫测试）

- [ ] **Step 7：Commit**

```bash
git add agent/tools/builtin/fitness_tools.py tests/tools/test_fitness_tools.py
git commit -m "fix: skip fitness profile confirmation guard during first-time initialization"
```

---

## Task 2：系统提示词重构（P1）

**Files:**
- Modify: `agent/core/context.py`
- Modify: `agent/app.py`
- Modify: `tests/core/test_context.py`

### Step 组：先更新测试（TDD 方向）

- [ ] **Step 1：更新 test_context.py 中会受影响的断言**

打开 `tests/core/test_context.py`，做以下修改：

**1a. `test_context_assembler_includes_system_memory_skills_history_and_user_message`**

将：
```python
    assert "[PROFILE.md]" in context[0]["content"]
```
改为：
```python
    assert "prefers python" in context[0]["content"]  # profile 内容仍注入，但无标签
    assert "[PROFILE.md]" not in context[0]["content"]  # 不再有文件标签
```

**1b. `test_context_assembler_marks_soul_as_identity_source_of_truth`**

将整个测试替换为验证 soul 内容出现在最前面、无 `[身份事实来源]` 块：

```python
def test_context_assembler_puts_soul_content_first_without_label() -> None:
    """Soul 内容应作为 system prompt 第一段出现，不带 [SOUL.md] 标签。"""
    assembler = ContextAssembler(system_prompt="")

    context = assembler.assemble(
        soul_text="# Identity\n你是银月。",
        memory_text="# User Profile\n",
        tool_index="可用工具：",
        skill_index="可用技能：",
        history=[{"role": "assistant", "content": "我是曾国藩。"}],
        user_message="你是谁",
    )

    system_content = context[0]["content"]
    # Soul 内容应出现在最前面
    assert system_content.startswith("# Identity\n你是银月。")
    # 不应有文件标签和冗余元注释
    assert "[SOUL.md]" not in system_content
    assert "[身份事实来源]" not in system_content
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/core/test_context.py -v
```

预期：2–3 个测试 FAIL（被修改的断言不匹配当前实现）

- [ ] **Step 3：重构 context.py 的 assemble() 方法**

打开 `agent/core/context.py`，将 `assemble()` 方法中的 `system_content` 拼接段落替换为以下实现（从 `system_content = "\n\n".join(` 到结尾 `)`）：

```python
        profile_is_empty = memory_text.strip() in {"", "# User Profile"}

        parts = [soul_text]  # Soul 内容第一位，无标签

        if not profile_is_empty:
            parts.append(memory_text)  # 用户档案紧跟，无标签

        parts.extend([
            system_time_block,
            channel_block,
            human_block,
            memory_items_block,
        ])

        if self.system_prompt:
            parts.append(self.system_prompt)

        parts.extend([
            reminder_policy_block,
            "[技能索引]",
            skill_index,
        ])

        system_content = "\n\n".join(parts)
```

同时删除以下已不再使用的变量定义（`identity_source_block`）：

```python
        identity_source_block = "\n".join(
            [
                "[身份事实来源]",
                "当前 `SOUL.md` 是助手活跃身份、名称、人格和风格的权威来源。",
                "如果聊天历史、笔记、工具载荷或旧记忆与 `SOUL.md` 冲突，以 `SOUL.md` 为准。",
            ]
        )
```

- [ ] **Step 4：运行 context 测试确认通过**

```bash
python -m pytest tests/core/test_context.py -v
```

预期：全部通过

- [ ] **Step 5：精简 app.py 的 _build_system_prompt()**

打开 `agent/app.py`，将整个 `_build_system_prompt()` 函数替换为：

```python
def _build_system_prompt(agent_name: str) -> str:
    """构建行为准则提示块（身份已由 SOUL.md 覆盖，此处只保留工具路由和回复约束）。"""
    return "\n".join(
        [
            "[行为准则]",
            "必须以系统提供的当前时间作为日期、时间和年份判断的事实来源，不凭历史对话猜测今天是哪一天。",
            "",
            "涉及收入、支出、报销、转账、消费汇总等记账请求时，使用账本工具；账目必要字段不完整时先追问用户再提交。",
            "回答账本相关问题前，先用 `ledger_query_entries` 或 `ledger_summary` 检查已提交账目，不要在未查询前声称记录缺失。",
            "当用户明确要求记录笔记、搜索笔记或查阅过往笔记时，使用笔记工具；不要把笔记当作自动长期记忆。",
            "回答当前新闻、最新价格、天气或任何可能近期变化的信息前，必须调用 `web_search`；若不可用则明确说明无法核验。",
            "",
            "`PROFILE.md` 和检索到的有效记忆项中的明确事实应视为既定上下文，不要随口覆盖用户身份或档案。",
            "用户明确要求记笔记时必须保存为笔记，但不要用笔记静默改写助手身份、人格或用户档案。",
            "",
            "未实际调用工具时，不得声称已通过某工具查询。工具结果到手前，不用"记住了"等确定性表述冒充写入成功。",
            "不向普通用户暴露"长期记忆""检索注入""固化"等系统内部术语，除非用户主动追问记忆机制。",
            "当用户询问有哪些工具或技能时，只依据当前回合真正可见的工具与技能索引回答。",
        ]
    )
```

- [ ] **Step 6：运行完整测试套件**

```bash
python -m pytest tests/core/ tests/tools/ tests/test_app.py -v
```

预期：全部通过

- [ ] **Step 7：Commit**

```bash
git add agent/core/context.py agent/app.py tests/core/test_context.py
git commit -m "refactor: put soul content first in system prompt, remove file labels and identity meta block"
```

---

## Task 3：记忆系统整顿（P2）

**Files:**
- Modify: `agent/config/models.py`
- Modify: `agent/config/loader.py`
- Modify: `agent/core/loop.py`
- Modify: `agent/app.py`

### Step 组 A：config 层增加 default_user_id

- [ ] **Step 1：在 models.py 的 AgentSettings 增加字段**

打开 `agent/config/models.py`，找到 `AgentSettings` dataclass：

```python
@dataclass(slots=True)
class AgentSettings:
    """一期 Agent 自身运行所需的最小配置。"""

    name: str
    workspace_dir: Path
    max_iterations: int
    context_history_turns: int = 10
```

替换为：

```python
@dataclass(slots=True)
class AgentSettings:
    """一期 Agent 自身运行所需的最小配置。"""

    name: str
    workspace_dir: Path
    max_iterations: int
    context_history_turns: int = 10
    default_user_id: str = "user"
```

- [ ] **Step 2：在 loader.py 解析新字段**

打开 `agent/config/loader.py`，找到构建 `AgentSettings` 的调用。搜索 `AgentSettings(`，找到类似：

```python
    return AgentSettings(
        name=_require_str(agent_section, "name", "agent"),
        workspace_dir=_resolve_path(config_dir, _require_str(agent_section, "workspace_dir", "agent")),
        max_iterations=int(agent_section.get("max_iterations", 8)),
        context_history_turns=int(agent_section.get("context_history_turns", 10)),
    )
```

追加 `default_user_id` 参数：

```python
    return AgentSettings(
        name=_require_str(agent_section, "name", "agent"),
        workspace_dir=_resolve_path(config_dir, _require_str(agent_section, "workspace_dir", "agent")),
        max_iterations=int(agent_section.get("max_iterations", 8)),
        context_history_turns=int(agent_section.get("context_history_turns", 10)),
        default_user_id=str(agent_section.get("default_user_id", "user")),
    )
```

- [ ] **Step 3：运行 config 测试**

```bash
python -m pytest tests/config/ -v
```

预期：全部通过（default 值为 "user"，现有测试不含该字段，向后兼容）

### Step 组 B：AgentCore 存储和使用 default_user_id

- [ ] **Step 4：AgentCore.__init__ 增加 default_user_id 参数**

打开 `agent/core/loop.py`，找到 `AgentCore.__init__` 的参数列表，在 `system_prompt: str = "",` 后面追加：

```python
        default_user_id: str = "user",
```

在 `__init__` 的赋值段（`self.context_history_turns = context_history_turns` 附近）追加：

```python
        self.default_user_id = default_user_id
```

- [ ] **Step 5：_build_memory_items_text 使用 default_user_id**

在 `loop.py` 的 `_build_memory_items_text` 方法中，找到：

```python
            user_scope = sender_id or "unknown"
```

替换为：

```python
            user_scope = self.default_user_id
```

同时找到该方法中 `self.mem0_memory_service.search(` 调用：

```python
            outcome = self.mem0_memory_service.search(
                user_message,
                user_id=user_scope,
                run_id=thread_id,
            )
```

（`user_scope` 已经更新，这里不需要额外改动）

- [ ] **Step 6：_extract_memories 使用 default_user_id，移除 mem0 可用时的 local fallback**

在 `loop.py` 的 `_extract_memories` 方法中，找到 mem0 写入调用：

```python
                outcome = self.mem0_memory_service.add_memory_items(
                    [...],
                    user_id=sender_id or "unknown",
                    run_id=thread_id,
                )
```

将 `user_id=sender_id or "unknown"` 替换为 `user_id=self.default_user_id`。

然后找到 mem0 写入失败后的 fallback 段落（`if outcome.get("ok"):` 之后直到 `if self.memory_store is None: return`）：

当前代码约为：
```python
                if outcome.get("ok"):
                    logger.info(...)
                    self.react_logger.record(...)
                    return
                logger.warning(...)
```

在 `return` 之后，找到：
```python
            if self.memory_store is None:
                return
            local_write_count = 0
            for candidate in candidates:
                ...
```

在这段 local store 写入之前，加一个短路条件：若 mem0 配置了且可用，不管写入结果如何都跳过 local store：

```python
            # mem0 已配置时，local store 仅作降级后端，不叠加写入
            if self.mem0_memory_service is not None and self.mem0_memory_service.is_ready:
                return
            if self.memory_store is None:
                return
```

- [ ] **Step 7：app.py 传递 default_user_id 给 AgentCore**

打开 `agent/app.py`，找到 `_build_app_from_settings_async` 中构建 `AgentCore` 的调用，在 `max_iterations=settings.agent.max_iterations,` 后追加：

```python
        default_user_id=settings.agent.default_user_id,
```

同时找到 `AgentCore.build_for_test` 的 classmethod（在 `loop.py` 末尾），确认它有 `default_user_id: str = "user"` 参数——若没有则同样追加（保证测试工厂与生产工厂参数对齐）。

- [ ] **Step 8：运行 loop 和 app 相关测试**

```bash
python -m pytest tests/core/test_loop.py tests/core/test_loop_events.py tests/test_app.py -v
```

预期：全部通过

- [ ] **Step 9：运行完整测试套件确认无退化**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

预期：全部通过（或仅有与外部服务依赖相关的 skip）

- [ ] **Step 10：Commit**

```bash
git add agent/config/models.py agent/config/loader.py agent/core/loop.py agent/app.py
git commit -m "feat: unify mem0 user_id via default_user_id config, make mem0/local backends mutually exclusive"
```

---

## 自检 Checklist

- [x] **Spec coverage**
  - Part 1（健身初始化）：Task 1 覆盖
  - Part 2 M1（sender_id 漂移）：Task 3 Step 5-6 覆盖
  - Part 2 M2（空 PROFILE 注入）：Task 2 Step 3 `profile_is_empty` 覆盖
  - Part 2 M3（后端互斥）：Task 3 Step 6 覆盖
  - Part 3 P1（SOUL 标签）：Task 2 Step 3 覆盖
  - Part 3 P2（身份事实来源块）：Task 2 Step 3 覆盖
  - Part 3 P3（规则前置）：Task 2 Step 3 新顺序覆盖
  - Part 3 P4（PROFILE 标签）：Task 2 Step 3 `profile_is_empty` 覆盖

- [x] **No placeholders**：所有 Step 包含完整代码

- [x] **Type consistency**：`default_user_id: str` 贯穿 models → loader → AgentCore → app，类型一致

- [x] **Test updates**：context 测试中两处旧断言已更新，新测试验证新行为
