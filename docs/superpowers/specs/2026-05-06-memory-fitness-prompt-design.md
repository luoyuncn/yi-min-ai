# 设计文档：记忆系统整顿 + 健身初始化修复 + 系统提示词重构

**日期**：2026-05-06  
**状态**：待实施

---

## 背景

三个独立但相关的问题：

1. 记忆系统存在双后端混乱、跨渠道 sender_id 漂移、空 PROFILE.md 每轮注入噪音等缺陷
2. 健身模块初始化流程因确认守卫误触发，导致用户反复被追问信息
3. 系统提示词结构生硬——SOUL.md 被文件标签包裹，模型把它当"读到的文件"而非自己的身份

---

## Part 1：健身初始化修复

### 问题根因

`fitness_profile_update`（`agent/tools/builtin/fitness_tools.py`）对 major fields（`goal`、`level`、`equipment`、`injuries`、`movement_restrictions`、`plan_style`、`primary_coach`、`coach_mix_rules`）设有确认守卫，目的是防止意外覆盖已有档案。

但首次初始化时档案为空，守卫依然触发，工具返回"已暂存，请回复确认"。LLM 把这个工具结果理解为"保存未完成，需要更多信息"，继续追问用户，形成死循环。

### 修复设计

在 `fitness_profile_update` 里，触发确认守卫前先判断当前档案是否为空（初始化场景）。判断条件：`goal`、`level`、`equipment` 三个核心字段均为 `None` 或空字符串，即视为空档案。

**逻辑流程**：
```
fitness_profile_update 被调用
  → 有 context 且 staged_updates 含 major fields？
      → 是：读当前 profile（store.get_profile()）
          → goal/level/equipment 均为空 → 初始化场景，跳过守卫，直接写入
          → 有已有数据 → 触发确认守卫（行为不变）
      → 否：直接写入（行为不变）
```

### 改动文件

- `agent/tools/builtin/fitness_tools.py`：在 `fitness_profile_update` 第 62 行附近增加约 8 行判断逻辑

---

## Part 2：记忆系统分层整顿

### 问题清单

| 编号 | 问题 | 影响 |
|------|------|------|
| M1 | sender_id 跨渠道漂移 | CLI/飞书同一用户记忆完全隔离，两份互不认识 |
| M2 | 空 PROFILE.md 每轮注入 | system prompt 多 `# User Profile\n` 噪音块 |
| M3 | 双后端语义不清 | mem0 失败时 fallback 写 local store，可能积累重复记忆 |

### M1 修复：统一 user_id

**方案**：在 `agent/config/models.py` 的 agent 配置里增加 `default_user_id`（默认 `"user"`）。`loop.py` 的 `_build_memory_items_text` 和 `_extract_memories` 使用此值作为 mem0 的 `user_id`，不再依赖原始 sender 字符串。

单用户部署（当前实际场景）统一走 `"user"` 命名空间，CLI 和飞书共享同一份记忆。

**配置示例**：
```yaml
agent:
  default_user_id: "user"   # 新增，mem0 user 命名空间
```

### M2 修复：跳过空 PROFILE.md

在 `agent/core/context.py` 的 `assemble()` 里，当 `memory_text.strip()` 的值等于 `"# User Profile"` 或为空字符串时，视为无实质内容，不注入 profile 块。

### M3 修复：后端互斥，不叠加

明确两个后端职责：
- **mem0**：唯一的自动提取记忆库（语义向量检索）
- **local MemoryStore**：仅在 `mem0_memory_service is None` 时启用（降级模式）

在 `loop.py` 的 `_extract_memories` 里：若 `mem0_memory_service.is_ready`，无论 mem0 写入结果如何都不再 fallback 写 local store。local store 只在 mem0 根本未配置时才是主路径。

### 改动文件

- `agent/config/models.py`：`AgentSettings` 增加 `default_user_id: str = "user"`
- `agent/app.py`：`_build_app_from_settings_async` 把 `settings.agent.default_user_id` 传给 `AgentCore.__init__`
- `agent/core/loop.py`（`AgentCore`）：
  - `__init__`：增加 `default_user_id: str = "user"` 参数并存为 `self.default_user_id`
  - `_build_memory_items_text`：用 `self.default_user_id` 替换 `sender_id or "unknown"` 作为 mem0 `user_id`
  - `_extract_memories`：用 `self.default_user_id` 替换 `sender_id or "unknown"`；mem0 可用时移除 local store fallback 写入
- `agent/core/context.py`：`assemble()` 增加空 profile 判断

---

## Part 3：系统提示词结构重构

### 问题清单

| 编号 | 问题 | 影响 |
|------|------|------|
| P1 | `[SOUL.md]` 标签暴露文件名 | 模型把身份当"读到的文件"，人格注入效果打折 |
| P2 | `[身份事实来源]` 元注释块 | 完全冗余，解释 SOUL.md 是权威来源，删掉更干净 |
| P3 | base system_prompt 约 40 行规则前置 | 规则比身份先读，人格定锚滞后 |
| P4 | `[PROFILE.md]` 标签 + 空内容 | 每轮注入空文件标签，纯噪音 |

### 新 system prompt 组装顺序

```
{soul_text}                    ← 第一段，无任何标签，模型的身份即从这里开始

{profile_section}              ← 仅当 PROFILE.md 非空时注入，无标签，紧跟身份后

[系统时间]                     ← 时间信息（不变）
[渠道上下文]                   ← 渠道格式要求（不变）
[用户上下文]                   ← 发送者信息（不变）

[检索到的长期记忆]             ← mem0 检索结果（语义不变）

[行为准则]                     ← 精简后的核心规则（替代原 base system_prompt）
[提醒路由]                     ← 不变

[技能索引]                     ← 不变
```

**移除**：
- `identity_source_block`（`[身份事实来源]` 整块）
- `[SOUL.md]` 标签行
- `[PROFILE.md]` 标签行

### base system_prompt 精简

`_build_system_prompt()` 保留以下内容，重命名为"行为准则"块，目标 15 行以内：

1. 时间基准：锚定当前日期，不凭历史猜今天
2. 工具路由：账本工具触发条件、web_search 触发条件、笔记工具边界、提醒工具边界
3. 记忆边界：PROFILE.md 事实视为既定，不随口改写身份
4. 回复风格：不暴露系统术语、不伪造工具调用、不用"记住了"确认未核验写入

**删除**：
- "你是一个智能Agent，你拥有自己的思想..." → Soul 已覆盖
- `f"进程启动本地时间：{now.strftime(...)}"` → 移入 `system_time_block`

### 改动文件

- `agent/core/context.py`：`assemble()` 重排拼接顺序，移除 3 个冗余块
- `agent/app.py`：`_build_system_prompt()` 精简内容，删除身份描述行和启动时间行

---

## 实施优先级

| 优先级 | 内容 | 风险 |
|--------|------|------|
| P0 | Part 1：健身初始化修复 | 低，改动范围小 |
| P1 | Part 3：提示词重构 | 中，影响所有对话，需测试人格一致性 |
| P2 | Part 2：记忆系统整顿 | 中，涉及配置变更，需验证 mem0 user_id 统一后记忆检索正常 |

---

## 不在本次范围内

- 记忆 TTL / 置信度衰减
- 多用户跨渠道身份映射（复杂场景，当前是单用户部署）
- PROFILE.md 自动填充流程（需单独设计）
- M-flow / local MemoryStore 的迁移与数据清理
