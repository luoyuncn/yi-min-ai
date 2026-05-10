# yi-min-ai

Yi Min AI Assistant，当前已经是一套可运行的本地 / 飞书 Agent 工程，不再只是阶段性骨架。

## 当前能力

- 统一入口：CLI、Web、Gateway、All 模式
- 单飞书生产渠道，对应一个清晰的 Yi Min 主体和一个 workspace
- Always-on Memory：`SOUL.md` / `MEMORY.md`
- 统一 `agent.db` 会话归档、长期笔记、记账数据
- M-flow 深度记忆接入
- Feishu 流式占位回复与结构化卡片
- 默认记账 / 笔记 / 健身教练 skill 自动脚手架
- **Web Search**：Tavily 优先、DuckDuckGo 兜底的结构化网页搜索工具
- **主动性调度**：agent 随机间隔自主唤醒，自由探索后决定是否主动联系用户
- `message_send` 工具：agent 可在任意工具调用中主动推送消息到飞书
- Linux 常驻部署脚本与 `yimin start|stop|restart|status|logs`

## 环境要求

- Python `3.12+`
- `uv`
- Linux 常驻部署时需要 `systemd`

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 快速开始

安装依赖：

```bash
uv sync
```

生成本地环境变量文件：

```bash
cp .env.example .env
```

然后按你的 provider / 飞书机器人填入密钥。

如果要启用高质量网页搜索，填入 Tavily key；不填也可以运行，系统会直接使用 DuckDuckGo 兜底搜索：

```bash
TAVILY_API_KEY=your-tavily-api-key
```

本地最小验证：

```bash
uv run python -m agent.main --mode cli --testing
```

真实运行：

```bash
uv run python -m agent.main
```

常用入口：

```bash
uv run python -m agent.main --mode cli --testing
uv run python -m agent.main --mode web
uv run python -m agent.main --mode gateway
uv run python -m agent.main --mode all
uv run python -m agent.gateway.main          # 飞书 + 主动性调度（默认开启）
uv run python -m agent.gateway.main --no-proactive  # 关闭主动性调度
```

跑测试：

```bash
uv run pytest -v
```

## 配置文件

- `config/agent.yaml`
  用于本地开发，默认工作空间为 `workspace/`。
- `config/agent.linux.yaml`
  用于 Linux 常驻部署，默认接入一个飞书机器人，运行数据固定写到当前仓库里的 `workspace/`。
- `config/providers.yaml`
  管理 provider 列表、模型名、`api_key_env`、`base_url` 等。

### Web Search

`web_search` 是一个只读联网工具，用于搜索网页并把结构化结果回灌给模型。系统提示词会要求模型在回答当前新闻、最新价格、天气、政策变化等可能近期变化的信息前先调用它。

实现位置：

- 工具实现：`agent/tools/builtin/web_tools.py`
- 工具注册：`agent/tools/registry.py`
- 单元测试：`tests/tools/test_web_tools.py`

Provider 顺序：

```text
TAVILY_API_KEY 已配置:
  Tavily -> DuckDuckGo fallback

TAVILY_API_KEY 未配置:
  DuckDuckGo

WEB_SEARCH_PROVIDER=duckduckgo:
  DuckDuckGo
```

Tavily 使用官方 `POST https://api.tavily.com/search` 接口；DuckDuckGo 走 `ddgs` 包的 `DDGS().text(...)`，无需 API key。工具本身不打开浏览器、不执行 JavaScript、不抓取每个结果页正文，也不在工具层做总结，只返回可引用的搜索结果。

环境变量：

```bash
# 可选；配置后优先使用 Tavily
TAVILY_API_KEY=your-tavily-api-key

# 可选；默认 tavily。设为 duckduckgo 可强制只用 DuckDuckGo
WEB_SEARCH_PROVIDER=tavily

# 可选；默认 https://api.tavily.com/search，主要用于代理或 mock 测试
TAVILY_SEARCH_URL=https://api.tavily.com/search

# 可选；默认 basic
TAVILY_SEARCH_DEPTH=basic

# 可选；默认 20 秒，最小按 1 秒处理
WEB_SEARCH_TIMEOUT_SECONDS=20
```

工具 schema：

```json
{
  "query": "搜索关键词，必填，至少 2 个字符",
  "num_results": "结果数量，默认 5，最大 8",
  "allowed_domains": ["只允许这些域名或其子域名"],
  "blocked_domains": ["排除这些域名或其子域名"],
  "topic": "可选 Tavily topic，例如 general、news、finance",
  "time_range": "可选 Tavily time_range，例如 day、week、month、year"
}
```

过滤规则：

- `allowed_domains` 和 `blocked_domains` 会先归一化，例如 `https://DOCS.python.org/` 变成 `docs.python.org`
- host 命中规则为 `host == domain` 或 `host.endswith("." + domain)`
- Tavily 请求会把白名单/黑名单映射到 `include_domains` / `exclude_domains`
- 工具返回前仍会本地再过滤一次，保证不同 provider 行为一致
- URL 去重时会忽略 fragment，例如 `https://example.com/a#one` 和 `https://example.com/a#two` 视为同一个结果
- 最终结果按 provider 原始顺序截断到 `num_results`，上限 8

工具返回值是 JSON 字符串，形态如下：

```json
{
  "query": "python json",
  "provider": "tavily",
  "provider_chain": ["tavily"],
  "fallback_used": false,
  "results": [
    {
      "title": "json - Python documentation",
      "url": "https://docs.python.org/3/library/json.html",
      "snippet": "JSON encoder and decoder documentation.",
      "source_host": "docs.python.org",
      "score": 0.9
    }
  ],
  "duration_seconds": 0.42,
  "commentary": "Search results for ... Include a Sources section ..."
}
```

失败行为：

- Tavily 网络错误、超时、HTTP 错误、无效 JSON、空结果都会记录到 `provider_errors`
- Tavily 失败后会自动尝试 DuckDuckGo
- 所有 provider 都失败或都没有可用结果时，返回带 `error` 字段的 JSON，主循环会把它识别为工具失败
- 查询参数非法时也返回带 `error` 字段的 JSON，不会让 Agent 进程崩溃

测试命令：

```bash
uv run pytest tests/tools/test_web_tools.py tests/tools/test_registry.py -q
uv run pytest -q
```

### M-flow 与 DashScope 说明

如果你给 M-flow 的 embedding 走阿里云 DashScope 的 OpenAI-compatible 接口，建议显式保持下面两个值：

```yaml
mflow:
  embedding:
    model: "text-embedding-v4"
    dimensions: 1024
    batch_size: 10
```

原因：

- `text-embedding-v4` 默认返回 `1024` 维向量
- DashScope 单次 embedding 批量上限是 `10`
- 如果你改过 embedding 维度或升级过 M-flow，旧的 `mflow_data/` 可能会和当前 schema 冲突，这时需要备份后重建对应 workspace 的 `mflow_data/`

## 工作区与数据策略

应用启动时会自动脚手架这些文件：

- `SOUL.md`
- `MEMORY.md`
- `HEARTBEAT.md`
- `CRON.yaml`
- `skills/bookkeeping/SKILL.md`
- `skills/note-taking/SKILL.md`
- `skills/fitness-coach/SKILL.md`（健身教练 RPG skill，含等级/经验/叙事系统）

运行态数据现在按下面的原则处理：

- `workspace/` 不进入 Git
- 本地数据库、日志、M-flow 数据、运行期 skill 都不会上传到远端
- Linux 部署和本地开发都把运行态文件固定在当前仓库目录里的 `workspace/` 下

如果你在 Linux 上用 `config/agent.linux.yaml`，默认会使用这些项目内目录：

```text
./workspace
```

历史上的 `workspace-main/`、`workspace-ops/` 只作为迁移来源保留，不再是默认运行目录。需要迁移时，优先把 `workspace-main/MEMORY.md` 和 `workspace-main/agent.db` 中的有效数据合并到 `workspace/`。

## Linux 一键部署

安装并注册用户态或 system 级 systemd 服务：

```bash
./scripts/install_linux.sh
```

如果 Linux 服务器访问 PyPI / `files.pythonhosted.org` 很慢，可以直接带镜像重试：

```bash
sudo UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh
```

如果还涉及系统证书链问题，再加：

```bash
sudo UV_NATIVE_TLS=true UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh
```

安装完成后可直接用：

```bash
yimin status
yimin start
yimin stop
yimin restart
yimin logs
```

默认行为：

- 普通用户执行时：安装 `systemd --user` 服务到 `~/.config/systemd/user/yimin.service`
- `sudo ./scripts/install_linux.sh` 时：安装 system 级服务到 `/etc/systemd/system/yimin.service`
- 启动命令都使用 `config/agent.linux.yaml`
- 运行态数据固定写到仓库内的 `workspace/`

如果你使用的是用户态 service，并且希望退出登录后继续运行，执行一次：

```bash
loginctl enable-linger "$USER"
```

## 升级方式

代码升级：

```bash
git pull
uv sync
yimin restart
```

这套流程只更新代码和依赖，不会覆盖 Git 未跟踪的 runtime 资产目录。

如果是 system 级安装：

```bash
git pull
uv sync
sudo yimin restart
```

## 主动性调度

Agent 会在随机间隔（默认 20~90 分钟）自动唤醒，给自己一段"自由时间"：可以搜索感兴趣的内容、回顾用户近期状态，或只是想想有没有什么值得分享的。做完之后自己决定发不发消息给用户。

- 默认开启，Gateway 启动即生效
- 静默时段：凌晨 0~7 点不唤醒（可在 `config/agent.yaml` 的 `proactive.quiet_hours` 调整）
- `session_id` 留空时自动使用最近的飞书会话，无需手动配置
- agent 回复 `[不打扰]` 则静默退出，不发消息

相关配置（`config/agent.yaml`）：

```yaml
proactive:
  enabled: true
  min_interval_minutes: 20
  max_interval_minutes: 90
  quiet_hours: [0, 1, 2, 3, 4, 5, 6, 7]
  session_id: ""   # 留空自动取最近会话
  channel: "feishu"
```

## 健身教练 Skill

`workspace/skills/fitness-coach/SKILL.md` 定义了一套 RPG 叙事层：等级、经验值、负面状态、银月教练人格。数据存储走 `fitness_tools`（Python 工具层），叙事包装由 SKILL.md 驱动。

- 源文件：`agent/skills/defaults/fitness-coach/SKILL.md`
- 首次启动自动复制到 `workspace/skills/fitness-coach/`
- `workspace/` 被 `.gitignore`，本地磁盘有，Git 里看不见

## 说明

- 默认单主体配置下，Heartbeat / Cron 可以使用同一个 `workspace/` 上下文；高级多 runtime 配置仍会自动禁用 Heartbeat / Cron
- `agent.main` / `agent.gateway.main` 现在都会按配置解析 workspace，不再硬编码 `workspace/`
- shell 脚本通过 `.gitattributes` 固定为 LF，避免 Linux 执行报错

## 相关文档

- [DEPLOY_LINUX.md](/D:/dev/agent/yi-min-ai/DEPLOY_LINUX.md)
- [config/agent.yaml](/D:/dev/agent/yi-min-ai/config/agent.yaml)
- [config/agent.linux.yaml](/D:/dev/agent/yi-min-ai/config/agent.linux.yaml)
- [.env.example](/D:/dev/agent/yi-min-ai/.env.example)
