# Linux 部署指南

这份文档对应当前仓库里的最新部署方式：`systemd --user` + `yimin` 生命周期命令。

## 目标

- 用户拉完代码后可以直接装服务
- 服务常驻运行，支持自动重启
- 代码与运行数据分离
- `git pull` 不覆盖用户本地资产

## 1. 准备环境

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

克隆项目：

```bash
git clone <your-repo-url> yi-min-ai
cd yi-min-ai
```

复制环境变量模板：

```bash
cp .env.example .env
```

填入你的 provider / 飞书凭据。

## 2. 一键安装

```bash
./scripts/install_linux.sh
```

这个脚本会做几件事：

1. `uv sync`
2. 把仓库里的 `scripts/yimin` 链接到 `~/.local/bin/yimin`
3. 写入 `~/.config/systemd/user/yimin.service`
4. 注册并启动 `yimin` 服务

## 3. 生命周期命令

```bash
yimin status
yimin start
yimin stop
yimin restart
yimin logs
```

如果你只想安装 service 不立刻启动，也可以手动执行：

```bash
./scripts/yimin install
```

卸载：

```bash
./scripts/yimin uninstall
```

## 4. 数据目录

Linux 部署默认使用外部数据目录：

```text
~/.local/share/yi-min-ai
```

如果你想改位置，先设置：

```bash
export YIMIN_DATA_ROOT=/data/yi-min-ai
```

然后再执行安装命令。

`config/agent.linux.yaml` 会把：

- 默认 workspace
- `feishu-main`
- `feishu-ops`

都映射到 `YIMIN_DATA_ROOT` 下的独立子目录。

## 5. 为什么这样更安全

代码目录仍然由 Git 管理，但运行态数据不在 Git 跟踪范围内：

- 数据库不会被提交
- M-flow 数据不会被提交
- 本地 skill 运行态副本不会被提交
- 升级时只更新代码，不覆盖用户资产

标准升级流程：

```bash
git pull
uv sync
yimin restart
```

## 6. 日志与排障

查看 service 日志：

```bash
yimin logs
```

等价于：

```bash
journalctl --user -u yimin -f
```

查看 service 状态：

```bash
yimin status
```

如果用户态 service 在退出登录后不会继续运行，执行：

```bash
loginctl enable-linger "$USER"
```

## 7. 额外说明

- 当前仓库默认的 `config/agent.yaml` 更适合本地开发
- 生产建议用 `config/agent.linux.yaml`
- 当前多 runtime 模式下，Heartbeat / Cron 仍会自动禁用，这是现阶段实现限制
