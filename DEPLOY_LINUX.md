# Linux 部署指南

这份文档对应当前仓库里的最新部署方式：`yimin` 生命周期命令，同时支持用户态 `systemd --user` 和 `sudo` 下的 system 级 service。

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

用户态安装：

```bash
./scripts/install_linux.sh
```

system 级安装：

```bash
sudo ./scripts/install_linux.sh
```

如果服务器访问 PyPI / `files.pythonhosted.org` 不稳定，可以直接切镜像：

```bash
sudo UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh
```

如果网络环境依赖系统证书链，再加：

```bash
sudo UV_NATIVE_TLS=true UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh
```

脚本也支持同义的自定义环境变量，方便和已有 `UV_*` 配置隔离：

```bash
sudo YIMIN_UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/install_linux.sh
sudo YIMIN_UV_NATIVE_TLS=true ./scripts/install_linux.sh
```

这个脚本会做几件事：

1. `uv sync`
2. 普通用户模式下把仓库里的 `scripts/yimin` 链接到 `~/.local/bin/yimin`
3. `sudo` 模式下把 `yimin` 链接到 `/usr/local/bin/yimin`
4. 根据权限写入用户态或 system 级 service
5. 注册并启动 `yimin` 服务

## 3. 启动两个独立服务

如果要给不同的人各跑一个单主体 Agent，推荐使用两份项目目录，每份项目有自己的 `.env`、`workspace/` 和 systemd service。安装时用不同的 `--service-name` 区分即可。

示例：启动两套 system 级服务。

```bash
# 第一套
cd /home/alice/yi-min-ai
cp .env.example .env
vim .env
sudo ./scripts/install_linux.sh --service-name yimin-alice
```

```bash
# 第二套
cd /home/bob/yi-min-ai
cp .env.example .env
vim .env
sudo ./scripts/install_linux.sh --service-name yimin-bob
```

这样会得到两套互相隔离的运行态数据：

```text
/home/alice/yi-min-ai/.env
/home/alice/yi-min-ai/workspace/
/etc/systemd/system/yimin-alice.service

/home/bob/yi-min-ai/.env
/home/bob/yi-min-ai/workspace/
/etc/systemd/system/yimin-bob.service
```

管理服务时带上对应 service name：

```bash
sudo yimin status --service-name yimin-alice
sudo yimin logs --service-name yimin-alice
sudo yimin restart --service-name yimin-alice
```

```bash
sudo yimin status --service-name yimin-bob
sudo yimin logs --service-name yimin-bob
sudo yimin restart --service-name yimin-bob
```

升级某一套实例时，进入对应项目目录操作：

```bash
cd /home/alice/yi-min-ai
git pull
uv sync
sudo yimin restart --service-name yimin-alice
```

注意：安装第二套时，`/usr/local/bin/yimin` 这个快捷命令会被更新为指向后安装的项目目录；这不影响已经安装好的 service，因为 service 文件里写入的是各自的 `WorkingDirectory` 和启动脚本路径。如果要重新安装某一套实例，回到那套项目目录执行 `sudo ./scripts/install_linux.sh --service-name ...`。

用户态 service 也支持同样方式，只是不加 `sudo`：

```bash
./scripts/install_linux.sh --service-name yimin-alice
./scripts/install_linux.sh --service-name yimin-bob
yimin status --service-name yimin-alice
```

## 4. 生命周期命令

用户态：

```bash
yimin status
yimin start
yimin stop
yimin restart
yimin logs
```

system 级：

```bash
sudo yimin status
sudo yimin start
sudo yimin stop
sudo yimin restart
sudo yimin logs
```

如果你只想安装 service 不立刻启动，也可以手动执行：

```bash
./scripts/yimin install
```

或：

```bash
sudo ./scripts/yimin install --scope system --service-user "$USER"
```

卸载：

```bash
./scripts/yimin uninstall --service-name yimin-alice
```

## 5. 数据目录

Linux 部署固定把运行态数据写在当前仓库目录里：

```text
./workspace
```

默认生产形态是一个飞书机器人对应一个 Yi Min 主体，所有运行态数据都写入 `workspace/`。历史版本中的 `workspace-main/`、`workspace-ops/` 只作为迁移来源保留，不再是默认运行目录。

如果你从旧的多机器人配置升级，建议先备份旧目录，再把有效记忆合并到新目录：

```bash
cp -a workspace-main workspace-main.backup.$(date +%Y%m%d%H%M%S) || true
mkdir -p workspace
cp workspace-main/MEMORY.md workspace/MEMORY.md
```

如果启动多个服务，每份项目目录都有自己的 `workspace/`。不要让两套服务共用同一个项目目录和同一个 `workspace/`，否则记忆、账本、提醒、定时任务都会混在一起。

## 6. 为什么这样更安全

代码目录仍然由 Git 管理，但运行态数据不在 Git 跟踪范围内：

- 数据库不会被提交
- M-flow 数据不会被提交
- 本地 skill 运行态副本不会被提交
- 升级时只更新代码，不覆盖用户资产

标准升级流程：

```bash
git pull
uv sync
yimin restart --service-name yimin-alice
```

## 7. 日志与排障

查看 service 日志：

```bash
yimin logs
```

等价于：

```bash
journalctl --user -u yimin -f
```

system 级等价于：

```bash
sudo journalctl -u yimin -f
```

查看 service 状态：

```bash
yimin status
```

system 级：

```bash
sudo yimin status
```

多服务场景下指定 service name：

```bash
sudo journalctl -u yimin-alice -f
sudo journalctl -u yimin-bob -f
```

如果用户态 service 在退出登录后不会继续运行，执行：

```bash
loginctl enable-linger "$USER"
```

## 8. 额外说明

- 当前仓库默认的 `config/agent.yaml` 更适合本地开发
- 生产建议用 `config/agent.linux.yaml`
- `sudo ./scripts/install_linux.sh` 会自动尝试把 `uv sync` 退回到原调用用户执行，避免把仓库和 `.venv` 改成 root 所有
- `sudo ./scripts/install_linux.sh` 现在会显式透传常用的 `UV_*` 下载参数，便于镜像、证书和索引策略在 `sudo -u` 场景下继续生效
- Linux service 不再依赖 `YIMIN_DATA_ROOT` 之类的路径环境变量，运行态目录固定在当前仓库下
- 默认单主体模式下，Heartbeat / Cron 使用 `workspace/`；高级多 runtime 模式下仍会自动禁用 Heartbeat / Cron，这是现阶段实现限制
