# Changelog

## v1.1.1 - 2026-04-23

### Bug 修复

**AsyncIO 事件循环问题修复：**
- 🐛 修复 `build_app()` 在 Gateway 启动时的事件循环嵌套问题
- 🐛 修复飞书 WebSocket 在后台线程中的事件循环冲突
- ✨ 新增 `build_app_async()` 异步版本
- ✨ `build_app()` 保留为同步包装（自动检测事件循环）

**影响模块：**
- `agent/app.py` - 添加异步版本函数
- `agent/gateway/main.py` - 使用异步版本
- `agent/main.py` - 使用异步版本
- `agent/gateway/adapters/feishu.py` - 优化线程启动逻辑

**验证通过：**
- ✅ CLI 模式启动正常
- ✅ Web 模式启动正常
- ✅ Gateway 模式启动正常
- ✅ 飞书 WebSocket 连接正常

---

## v1.1.0 - 2026-04-23

### 重大更新

一期核心功能全部实现，系统从"CLI 基座"升级为"完整个人助理 Agent"。

### 新增功能

**记忆系统：**
- ✨ M-flow 认知记忆系统集成（Cone Graph + LanceDB）
- ✨ `recall_memory` 工具：图路由深度检索
- ✨ Compaction 上下文压缩引擎
- ✨ 异步增量写入 M-flow（每轮对话结束后）

**通道与网关：**
- ✨ 飞书 WebSocket 长连接适配器
- ✨ Gateway 多通道路由系统
- ✨ Command Queue（Session 串行执行保证）
- ✨ 群聊/私聊区分处理

**主动调度：**
- ✨ Heartbeat 定时轮询（HEARTBEAT.md 任务清单）
- ✨ Cron 精确时间调度（CRON.yaml 配置）
- ✨ 支持 Skill/Prompt/Tool 三种任务类型

**工具扩展：**
- ✨ `shell_exec` - Shell 命令执行（需审批）
- ✨ `web_search` - DuckDuckGo Web 搜索
- ✨ MCP Client 框架预留（config/mcp_servers.yaml）

**观测性：**
- ✨ Metrics 收集器（token/延迟/成本/成功率）
- ✨ Tracing 链路追踪（JSONL 持久化）
- ✨ 结构化日志（敏感数据脱敏）

### 优化改进

- 🔧 上下文组装器新增 token 计数功能
- 🔧 核心循环集成 Compaction 检查
- 🔧 Provider Manager 支持压缩专用模型
- 🔧 Session Archive 预留 Compaction lineage 表

### 配置文件

新增配置文件：
- `workspace/HEARTBEAT.md` - Heartbeat 任务清单
- `workspace/CRON.yaml` - Cron 定时任务
- `config/mcp_servers.yaml` - MCP Server 配置（预留）

### 依赖更新

新增依赖：
- `lark-oapi>=1.5.5` - 飞书 SDK
- `lancedb>=0.15.0` - M-flow 向量存储
- `python-croniter>=2.0.0` - Cron 表达式解析
- `pytz>=2024.1` - 时区支持
- `tiktoken>=0.7.0` - Token 计数
- `duckduckgo-search>=6.0.0` - Web 搜索

### 文档更新

- 📝 更新 README.md（完整功能清单）
- 📝 新增 CHANGELOG.md

---

## v1.0.0 - 2026-04-22

### 初始发布

阶段一 CLI 基座 + 本地 Web Agent Console：

- ✅ ReAct 核心循环
- ✅ Anthropic/OpenAI Provider 抽象层
- ✅ SQLite 会话归档 + FTS5 检索
- ✅ Always-On Memory（SOUL.md / MEMORY.md）
- ✅ Skill 按需加载
- ✅ 基础工具集（file_read/write, memory_write, search_sessions, read_skill）
- ✅ CLI 单通道入口
- ✅ CopilotKit Web UI（interrupt/approval/thread replay）
- ✅ 测试模式（无需 API Key）
