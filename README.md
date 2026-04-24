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
  用于本地开发，工作空间由 `channels.instances[*].workspace_dir` 决定。
- `config/agent.linux.yaml`
  用于 Linux 常驻部署，运行数据固定写到当前仓库里的 instance workspace 目录。
- `config/providers.yaml`
  管理 provider 列表、模型名、`api_key_env`、`base_url` 等。

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

运行态数据现在按下面的原则处理：

- `workspace-main/`、`workspace-ops/` 不进入 Git
- 本地数据库、日志、M-flow 数据、运行期 skill 都不会上传到远端
- Linux 部署和本地开发都把运行态文件固定在当前仓库目录里的各 instance workspace 下

如果你在 Linux 上用 `config/agent.linux.yaml`，默认会使用这些项目内目录：

```text
./workspace-main
./workspace-ops
```

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
- 运行态数据固定写到仓库内各实例自己的 `workspace-main/`、`workspace-ops/`

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

## 说明

- 多 runtime 配置下，Heartbeat / Cron 当前仍会自动禁用，这一点是现有实现约束
- `agent.main` / `agent.gateway.main` 现在都会按配置解析 workspace，不再硬编码 `workspace/`
- shell 脚本通过 `.gitattributes` 固定为 LF，避免 Linux 执行报错

## 相关文档

- [DEPLOY_LINUX.md](/D:/dev/agent/yi-min-ai/DEPLOY_LINUX.md)
- [config/agent.yaml](/D:/dev/agent/yi-min-ai/config/agent.yaml)
- [config/agent.linux.yaml](/D:/dev/agent/yi-min-ai/config/agent.linux.yaml)
- [.env.example](/D:/dev/agent/yi-min-ai/.env.example)
