# Agent账本与自动笔记设计

## 背景

当前系统已经具备多渠道独立 workspace、会话归档、工具调用和 skill 索引能力，但还没有真正的“长期业务数据层”。`MEMORY.md` 更像一份人工维护的长期摘要，不适合高频增量写入；`sessions.db` 只解决会话回放和全文检索，不适合结构化记账、笔记检索和统计分析。本次需要补两类能力：一类是结构化的记账工具，支持自然语言提取、追问补全、查询与统计；另一类是长期笔记能力，既支持用户主动要求“记住”，也支持对高价值事实做保守的自动记录。

本次设计还有一个硬约束：后续重构不能破坏当前主链路，升级必须平滑。这里的“平滑”不是指保留旧测试数据库文件，而是指尽量不改 `AgentCore -> ToolRegistry -> ToolExecutor -> SessionArchive` 的外部调用形状，不把已经跑通的 Gateway、Web、Session 管理与现有测试大面积掀翻。

## 核心目标

1. 每个 workspace 统一使用一个 `agent.db`，替代当前分散的 `sessions.db`。
2. 会话归档继续可用，外部接口尽量保持不变。
3. 新增账本能力：
   - 从自然语言中提取账目信息
   - 关键字段不完整时先追问
   - 只有确认后才正式入账
   - 支持查询与统计
4. 新增笔记能力：
   - 用户明确要求记录时必须保存
   - 重要长期事实可自动记录
   - 默认采用混合回执策略：明确要求必回执，重要自动记录简短回执，普通自动笔记静默
5. 尽量通过“新增表、新增工具、最小改造现有路径”的方式实现，而不是重写核心循环。

## 分层判断：Tool 还是 Skill

### 记账

记账必须做成 `tool`。原因很直接：它需要结构化字段、可靠落库、支持查询和统计，还要有“追问后再入账”的状态机。只靠 skill 或 `file_write` 这类通用工具会让数据越来越漂，不适合长期演进。

### 笔记

笔记采用 `tool + skill` 混合方案。`tool` 负责真实存储、检索、更新和近期列表；`skill` 负责告诉模型：

- 哪些信息值得记
- 哪些场景必须记
- 什么情况下自动记录
- 什么时候需要给用户回执

这样可以把“智能触发规则”和“可靠持久化”解耦。

## 存储方案

每个 workspace 使用一个统一数据库文件：

- `workspace/agent.db`

不做 `sessions.db -> agent.db` 的迁移逻辑；旧库视为测试阶段遗留，可以忽略。代码层面只要求平滑升级，不要求兼容旧测试数据文件。

### 表结构

第一版最小集合如下：

1. `sessions`
2. `sessions_fts`
3. `ledger_drafts`
4. `ledger_entries`
5. `notes`
6. `notes_fts`

#### sessions / sessions_fts

继续承担现有会话归档和全文检索职责，只是底层文件改为 `agent.db`。现有 `SessionArchive` 外部接口保持不变。

#### ledger_drafts

用于多轮追问期间保存未完成账目草稿，建议字段：

- `thread_id`
- `direction`
- `amount_cent`
- `currency`
- `category`
- `occurred_at`
- `merchant`
- `note`
- `missing_fields_json`
- `source_message_id`
- `updated_at`

一条线程同时只保留一条活跃 draft。

#### ledger_entries

正式账目表，建议字段：

- `id`
- `direction`
- `amount_cent`
- `currency`
- `category`
- `occurred_at`
- `merchant`
- `note`
- `source_message_id`
- `source_thread_id`
- `created_at`
- `updated_at`

金额统一用“分”为单位的整数，避免浮点误差。

#### notes / notes_fts

长期笔记表及其全文检索表。`notes` 建议字段：

- `id`
- `note_type`
- `title`
- `content`
- `importance`
- `is_user_explicit`
- `source_message_id`
- `source_thread_id`
- `created_at`
- `updated_at`

`note_type` 第一版先用有限类别：`preference / profile / plan / constraint / contact / misc`。

## 记账状态机

记账不允许“猜着入账”。流程固定为：

1. 模型识别出用户存在记账意图。
2. 调用 `ledger_upsert_draft` 保存当前已知字段。
3. 如果缺少关键字段，读取 draft 并追问用户。
4. 用户补充后再次更新 draft。
5. 仅当关键字段齐全时，调用 `ledger_commit_draft` 写入 `ledger_entries`。

关键字段第一版建议至少包括：

- `direction`
- `amount_cent`
- `occurred_at`

`currency` 可默认 `CNY`，但保留显式覆盖能力。

## 自动笔记规则

采用混合策略：

### 强制记录

以下场景必须调用 `note_add`：

- 用户明确说“记住”“记一下”“以后按这个来”“不要忘了”
- 用户提供长期约束，如饮食禁忌、偏好、固定工作方式、关键身份背景

### 自动记录

以下场景允许自动记录：

- 长期偏好
- 个人资料
- 持续计划
- 稳定约束
- 重要联系人与关系

### 默认不记录

以下内容不自动记：

- 一次性闲聊
- 瞬时情绪
- 无长期价值的临时事实
- 仅由模型推断出的信息

## 工具接口

### 记账工具

- `ledger_upsert_draft`
- `ledger_get_active_draft`
- `ledger_commit_draft`
- `ledger_query_entries`
- `ledger_summary`

### 笔记工具

- `note_add`
- `note_search`
- `note_list_recent`
- `note_update`

## 平滑升级原则

1. 不改 `AgentCore` 对会话归档的依赖形状。
2. 不重写 `SessionManager` 和 Web/Gateway 的线程逻辑。
3. 通过底层数据库文件切换和新增 repository/tool 完成功能扩展。
4. 所有新增能力优先走“加法”，避免推翻现有路径。
5. 任何后续重构必须先保证现有测试可继续通过。

## 最小实施顺序

1. 把 workspace 默认数据库收口到 `agent.db`。
2. 为统一数据库补充 `ledger_*` / `notes_*` 表。
3. 新增 ledger repository 与对应 tools。
4. 新增 notes repository 与对应 tools。
5. 把新工具接入默认注册表。
6. 为 workspace 补默认 `bookkeeping` / `note-taking` skill。
7. 用测试覆盖统一数据库、记账工具、笔记工具与现有主链路回归。
