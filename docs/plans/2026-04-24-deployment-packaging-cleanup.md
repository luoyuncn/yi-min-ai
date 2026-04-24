# Deployment Packaging Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让仓库只提交代码与模板，运行态数据不再污染 Git，并为 Linux 提供 `yimin start|stop|restart` 的常驻部署能力。

**Architecture:** 保持现有 Agent/Gateway 主链路不大改，新增一层部署辅助能力。配置加载器负责解析带环境变量的路径；运行时继续按 workspace 自动脚手架；Linux 侧通过 `systemd --user` + `yimin` 包装命令把 repo、依赖和运行数据分离。

**Tech Stack:** Python 3.12, uv, systemd user service, pytest

---

### Task 1: 锁定路径解析与工作区脚手架行为

**Files:**
- Modify: `tests/config/test_loader.py`
- Modify: `tests/test_app.py`

**Step 1: Write the failing tests**

- 新增配置测试，断言 `workspace_dir` / `mflow.data_dir` 可解析 `${VAR:-fallback}`。
- 新增应用装配测试，断言新 workspace 会自动生成 `HEARTBEAT.md` 与 `CRON.yaml`。

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/config/test_loader.py tests/test_app.py -v`

**Step 3: Implement minimal code**

- 在配置加载器里加入环境变量路径展开。
- 在 workspace 脚手架里补齐调度模板文件。

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_loader.py tests/test_app.py -v`

**Step 5: Commit**

```bash
git add tests/config/test_loader.py tests/test_app.py agent/config/loader.py agent/app.py
git commit -m "feat: support runtime path expansion and workspace scaffolding"
```

### Task 2: 收口入口层工作区解析

**Files:**
- Create: `tests/test_runtime_paths.py`
- Create: `agent/runtime_paths.py`
- Modify: `agent/main.py`
- Modify: `agent/gateway/main.py`
- Modify: `agent/config/__init__.py`

**Step 1: Write the failing test**

- 断言入口层会根据配置解析 workspace，而不是硬编码 `workspace/`。

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runtime_paths.py -v`

**Step 3: Write minimal implementation**

- 抽一个公共 helper，负责加载 `.env` + 解析配置文件中的 workspace 路径。
- 让 `agent.main` 与 `agent.gateway.main` 都使用这个 helper 初始化日志和调度器目录。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runtime_paths.py -v`

**Step 5: Commit**

```bash
git add tests/test_runtime_paths.py agent/runtime_paths.py agent/main.py agent/gateway/main.py agent/config/__init__.py
git commit -m "refactor: resolve entrypoint workspaces from config"
```

### Task 3: 增加 Linux 管理命令与服务模板

**Files:**
- Create: `tests/deploy/test_linux.py`
- Create: `agent/deploy/__init__.py`
- Create: `agent/deploy/linux.py`
- Create: `agent/deploy/cli.py`
- Create: `scripts/yimin`
- Create: `scripts/install_linux.sh`
- Modify: `pyproject.toml`

**Step 1: Write the failing tests**

- 断言 service unit 指向 repo 的 `.venv/bin/python`、`config/agent.linux.yaml` 与外部数据目录。
- 断言 `systemctl` / `journalctl` 命令拼装正确。

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/deploy/test_linux.py -v`

**Step 3: Write minimal implementation**

- 提供纯函数渲染 service unit 和命令。
- 提供 `yimin install|start|stop|restart|status|logs|uninstall` CLI。
- 提供 Linux 包装脚本和一键安装脚本。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/deploy/test_linux.py -v`

**Step 5: Commit**

```bash
git add tests/deploy/test_linux.py agent/deploy scripts/yimin scripts/install_linux.sh pyproject.toml
git commit -m "feat: add linux lifecycle management commands"
```

### Task 4: 清理 Git 运行态污染并补齐部署配置

**Files:**
- Modify: `.gitignore`
- Create: `config/agent.linux.yaml`
- Update index only: `workspace-main/**`, `workspace-min/**`

**Step 1: Write the failing verification**

- 用 `git ls-files` 检查运行态工作区文件仍被 Git 跟踪。

**Step 2: Run verification to confirm current bad state**

Run: `git ls-files workspace-main workspace-min`

**Step 3: Apply cleanup**

- 扩充 `.gitignore`，让 `workspace-main/`、`workspace-min/`、`.yimin-data/`、root workspace 的运行态产物不再进入 Git。
- 新增 Linux 专用配置，默认把运行数据落到 repo 外或隐藏目录。
- 用 `git rm --cached` 仅从索引里移除已跟踪的运行态文件，不删除本地数据。

**Step 4: Run verification to confirm clean state**

Run: `git status --short`

**Step 5: Commit**

```bash
git add .gitignore config/agent.linux.yaml
git rm --cached -r workspace-main workspace-min
git commit -m "chore: stop tracking runtime workspaces"
```

### Task 5: 更新文档并做最终验证

**Files:**
- Modify: `README.md`
- Modify: `DEPLOY_LINUX.md`
- Optionally modify: `.env.example`

**Step 1: Update docs**

- README 改成当前真实能力、当前启动方式、工作区/数据目录策略、Linux `yimin` 部署方法。
- DEPLOY_LINUX 改成安装脚本 + `yimin` 生命周期命令为主。

**Step 2: Run verification**

Run: `uv run pytest tests/config/test_loader.py tests/test_app.py tests/test_runtime_paths.py tests/deploy/test_linux.py -v`

Run: `uv run pytest tests/gateway/test_server.py tests/gateway/test_feishu_cards.py tests/memory/test_mflow_bridge.py -v`

Run: `git status --short`

**Step 3: Final commit**

```bash
git add README.md DEPLOY_LINUX.md .env.example
git commit -m "docs: refresh deployment and runtime guidance"
```
