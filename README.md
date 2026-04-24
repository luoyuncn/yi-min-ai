# yi-min-ai

Yi Min AI Assistant，当前已经是一套可运行的本地 / 飞书多通道 Agent 工程，不再只是阶段性骨架。

## 当前能力

- 统一入口：CLI、Web、Gateway、All 模式
- 多飞书实例接入与独立 workspace 隔离
- Always-on Memory：`SOUL.md` / `MEMORY.md`
- 统一 `agent.db` 会话归档、长期笔记、记账数据
- M-flow 深度记忆接入
- Feishu 流式占位回复与结构化卡片
- 默认记账 / 笔记 skill 自动脚手架
- Linux 常驻部署脚本与 `yimin start|stop|restart|status|logs`

## 环境要求

- Python `3.12+`
- `uv`
- Linux 常驻部署时需要 `systemd --user`

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

然后按你的 provider / 飞书实例填入密钥。

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
uv run python -m agent.gateway.main
```

跑测试：

```bash
uv run pytest -v
```

## 配置文件

- `config/agent.yaml`
  用于本地开发，默认 workspace 在仓库目录下。
- `config/agent.linux.yaml`
  用于 Linux 常驻部署，默认把运行数据放到 `YIMIN_DATA_ROOT` 指向的外部目录。
- `config/providers.yaml`
  管理 provider 列表、模型名、`api_key_env`、`base_url` 等。

## 工作区与数据策略

应用启动时会自动脚手架这些文件：

- `SOUL.md`
- `MEMORY.md`
- `HEARTBEAT.md`
- `CRON.yaml`
- `skills/bookkeeping/SKILL.md`
- `skills/note-taking/SKILL.md`

运行态数据现在按下面的原则处理：

- `workspace-main/`、`workspace-min/` 不再进入 Git
- 本地数据库、日志、M-flow 数据、运行期 skill 都不会上传到远端
- Linux 部署默认把数据放在仓库外部，避免 `git pull` 覆盖用户资产

如果你在 Linux 上用 `config/agent.linux.yaml`，默认数据目录是：

```text
~/.local/share/yi-min-ai
```

也可以通过环境变量覆写：

```bash
export YIMIN_DATA_ROOT=/data/yi-min-ai
```

## Linux 一键部署

安装并注册用户态 systemd 服务：

```bash
./scripts/install_linux.sh
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

- service 文件写到 `~/.config/systemd/user/yimin.service`
- 运行数据写到 `~/.local/share/yi-min-ai`
- 启动命令使用 `config/agent.linux.yaml`

如果你希望用户态服务在退出登录后继续运行，执行一次：

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

这套流程只更新代码和依赖，不会覆盖 `YIMIN_DATA_ROOT` 下的用户资产。

## 说明

- 多 runtime 配置下，Heartbeat / Cron 当前仍会自动禁用，这一点是现有实现约束
- `agent.main` / `agent.gateway.main` 现在都会按配置解析 workspace，不再硬编码 `workspace/`
- shell 脚本通过 `.gitattributes` 固定为 LF，避免 Linux 执行报错

## 相关文档

- [DEPLOY_LINUX.md](/D:/dev/agent/yi-min-ai/DEPLOY_LINUX.md)
- [config/agent.yaml](/D:/dev/agent/yi-min-ai/config/agent.yaml)
- [config/agent.linux.yaml](/D:/dev/agent/yi-min-ai/config/agent.linux.yaml)
- [.env.example](/D:/dev/agent/yi-min-ai/.env.example)
