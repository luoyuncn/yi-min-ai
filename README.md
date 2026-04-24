# yi-min-ai

Yi Min AI Assistant

## 当前状态

当前项目已经完成“阶段一 CLI 基座 + 本地 Web Agent Console 第二阶段”。

当前已经具备的能力：

- CLI 单通道对话入口
- 配置加载与校验
- Anthropic Provider 抽象层
- Stage One 安全工具集
- Skill Loader
- Always-On Memory：`SOUL.md` / `MEMORY.md`
- SQLite 会话归档与全文检索
- Session 恢复与多线程切换基础
- 测试模式下的完整可演示链路
- 基于 CopilotKit 的本地 Web Agent Console
- Web interrupt / approval / thread replay

当前明确还没有做的内容：

- 飞书 Adapter
- Approval Flow
- Compaction
- M-flow 集成
- Heartbeat / Cron
- Observability
- 多 Provider Fallback

## 目录说明

当前核心目录：

- `agent/`: 主代码
- `config/`: 运行配置
- `workspace/`: 运行时工作区、记忆文件、SQLite 数据库
- `tests/`: 阶段一测试
- `docs/plans/`: 实施计划文档

其中最值得先看的文件：

- `agent/app.py`
- `agent/core/loop.py`
- `agent/config/loader.py`
- `agent/tools/registry.py`
- `agent/memory/session_archive.py`

## 环境要求

### Windows 测试环境

- Python `3.12+`
- `uv` 包管理器
- Node.js `24+`（如果重新构建 Web 前端）

### Linux 生产环境

- Python `3.12+`
- `uv` 包管理器
- systemd（服务管理）

**安装 uv：**

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 安装依赖

在项目根目录执行：

```bash
uv sync
```

**注意**：一期完整版新增依赖：
- `lark-oapi` - 飞书 SDK
- `lancedb` - M-flow 向量存储
- `python-croniter` + `pytz` - Cron 调度
- `tiktoken` - Token 计数
- `duckduckgo-search` - Web 搜索

如果你只需要基础功能（CLI + Web），无需安装 M-flow 和飞书相关依赖。

## 环境变量

项目里的 Provider 密钥通过环境变量读取，变量名由 `config/providers.yaml` 里的 `api_key_env` 决定。

你可以先从模板生成本地 `.env`：

```powershell
Copy-Item .env.example .env
```

然后按需填写你要使用的密钥。

注意：

- `.env` 已经被 `.gitignore` 忽略，不会提交到仓库
- 启动时会自动加载 `config/.env` 和当前工作目录下的 `.env`
- 已经存在于进程环境里的变量优先级更高，不会被 `.env` 覆盖
- 使用自定义 OpenAI 兼容网关时，`base_url` 通常应指向 API 根路径，例如 `http://host:port/v1`

## 🚀 统一启动命令（推荐）

**一个命令启动所有功能：**

```bash
# 默认模式（Gateway + Heartbeat + Cron + 飞书）
uv run python -m agent.main

# 测试模式（无需 API Key）
uv run python -m agent.main --mode cli --testing

# 仅 Web UI
uv run python -m agent.main --mode web

# 同时启动 Web + Gateway
uv run python -m agent.main --mode all
```

**环境变量配置（生产模式）：**

```bash
# PowerShell (Windows)
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
$env:OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"

# Bash (Linux/macOS)
export FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
export OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
```

**或使用 `.env` 文件（推荐）：**

```bash
# 创建 .env 文件
cp .env.example .env
# 编辑填入实际凭证
nano .env
```

---

## 启动模式说明

### 模式 1: CLI（命令行交互）

```bash
uv run python -m agent.main --mode cli --testing
```

**用途：** 本地测试、开发调试

### 模式 2: Web（浏览器 UI）

```bash
uv run python -m agent.main --mode web --testing
```

**用途：** 可视化测试，访问 http://127.0.0.1:8000

### 模式 3: Gateway（生产推荐）⭐

```bash
# 默认模式（自动启用所有功能）
uv run python -m agent.main

# 等价于
uv run python -m agent.main \
  --mode gateway \
  --enable-feishu \
  --enable-heartbeat \
  --enable-cron
```

**用途：**
- ✅ 飞书实时接收消息
- ✅ Heartbeat 主动检查任务（默认 30 分钟）
- ✅ Cron 定时执行（如每天 8 点发简报）
- ✅ 适合 24/7 运行

### 模式 4: All（Web + Gateway 同时）

```bash
uv run python -m agent.main --mode all
```

**用途：** 既能用飞书，又能用浏览器查看

---

## 快速测试

### 1. 跑自动化测试

```bash
uv run pytest -v
```

### 2. CLI 测试模式

最快速的验证方式（无需任何配置）：

```bash
uv run python -m agent.cli.main --config config/agent.yaml --testing
```

启动后你可以直接输入：

```text
你好
读取 SOUL.md
读取 MEMORY.md
读取 不存在的文件.txt
exit
```

你应该重点观察：

- `你好` 可以得到普通文本回复
- `读取 SOUL.md` 可以走一次工具调用链
- 读取不存在的文件不会把进程打崩，而是被当作工具错误处理

### 2.1 构建并启动本地 Web Agent UI

如果你第一次拉起 Web UI，先构建前端：

```powershell
cd frontend
npm install
npm run build
cd ..
```

如果你想在浏览器里验证 Agent 型交互，可以直接启动内置 Web 入口：

```powershell
uv run python -m agent.web.main --config config/agent.yaml --testing
```

然后在浏览器打开：

```text
http://127.0.0.1:8000
```

当前这版 Web UI 提供：

- CopilotKit 驱动的聊天界面
- SQLite 线程历史恢复
- 多 thread 切换
- 工具调用流式展示
- `file_write` / `memory_write` 的 approval 卡片
- 运行中 interrupt / stop
- 基于 AG-UI 事件流的 SSE 输出

说明：

- Web 前端源码在 `frontend/`
- 构建产物输出到 `agent/web/static/app/`
- 后端仍然是原来的 Python runtime，没有迁移到 LangGraph
- 如果你改了前端代码，记得重新执行 `npm run build`

### 3. 检查 SQLite 是否已经落库

测试模式跑过几轮之后，应该已经生成：

- `workspace/sessions.db`

你可以这样检查：

```powershell
@'
import sqlite3
from pathlib import Path

db = Path("workspace/sessions.db")
print("DB_EXISTS =", db.exists())

conn = sqlite3.connect(db)
print("ROW_COUNT =", conn.execute("select count(*) from sessions").fetchone()[0])
print("FTS_MATCH =", conn.execute("select count(*) from sessions_fts where sessions_fts match 'Yi Min'").fetchone()[0])
'@ | uv run python -
```

正常情况下：

- `DB_EXISTS = True`
- `ROW_COUNT` 大于 `0`

### 4. 用真实主 Provider 启动

真实模式下，只会初始化 `providers.default_primary` 指向的那个 Provider。
默认配置里 `default_primary` 是 `gpt-5`，因此你需要准备的是 `OPENAI_API_KEY`。

PowerShell:

```powershell
$env:OPENAI_API_KEY="your-real-key"
uv run python -m agent.cli.main --config config/agent.yaml
```

建议你先输入：

```text
你好，请介绍一下你自己
读取 SOUL.md
```

说明：

- 不加 `--testing` 时，会走当前 `default_primary` 对应的真实 Provider
- 如果你把 `default_primary` 切回 `claude-sonnet`，则需要设置 `ANTHROPIC_API_KEY`
- 当前默认配置文件在 `config/providers.yaml`

## 可用工具集

### 基础工具（默认启用）

- `file_read` - 读取工作区文件
- `file_write` - 写入工作区文件（需审批）
- `memory_write` - 修改 MEMORY.md（需审批）
- `search_sessions` - SQLite FTS5 会话检索
- `read_skill` - 按需加载 Skill 完整内容

### M-flow 深度记忆

- `recall_memory` - 图路由深度检索，适用于因果推理和跨会话关联

### 可选工具（需配置启用）

- `shell_exec` - 执行 Shell 命令（需审批）
- `web_search` - DuckDuckGo Web 搜索

### MCP 工具（预留）

MCP Client 框架已实现，配置文件：`config/mcp_servers.yaml`。
二期接入外部 Server 时只需添加配置，无需改动代码。

## 测试模式和真实模式的区别

测试模式：

- 不需要 API Key
- 用内置 testing provider
- 能稳定演示普通回复和工具调用
- 适合你先验证系统骨架

真实模式：

- 需要当前 `default_primary` 对应 provider 的 API Key
- 默认配置下会走 `agent/providers/openai_compat.py`
- 如果切换到 Anthropic，则走 `agent/providers/anthropic.py`
- 更适合验证真实模型交互效果

## 工作区文件说明

阶段一默认依赖这些文件：

- `workspace/SOUL.md`
- `workspace/MEMORY.md`
- `workspace/skills/`
- `workspace/sessions.db`

其中：

- `SOUL.md` 定义人格与表达风格
- `MEMORY.md` 保存长期事实记忆
- `sessions.db` 保存会话归档

## 一些你现在就可以做的手动验证

### 验证多轮对话是否连续

进入 CLI 后连续输入：

```text
我喜欢 Python
你记住我喜欢什么了吗
```

当前阶段一的“连续性”主要来自同一进程中的 Session 复用。

### 验证技能读取边界

后续如果你扩 Skill，可以把技能放进：

- `workspace/skills/<skill-name>/SKILL.md`

当前实现已经限制 `read_skill` 只能读这个目录以内的内容。

### 验证文件工具边界

你可以尝试在测试模式里读取工作区文件，比如：

```text
读取 SOUL.md
```

当前文件工具被限制在 `workspace/` 范围内，不应该越界访问项目外内容。

## 当前已知说明

- 如果你没有设置当前 `default_primary` 所需的 API Key，真实模式无法启动，这是正常现象
- 阶段一重点是“本地可跑通的骨架”，不是完整产品形态
- 当前代码里的 Python 文件都已经补了较详细的中文注释，适合顺着阅读

## 下一阶段

按当前规划，后续阶段二会继续实现：

1. 飞书 Adapter
2. Approval Flow
3. Compaction
4. M-flow 集成

