# 启动命令示例

## 统一入口：`python -m agent.main`

**默认行为：** 启动 Gateway 模式（飞书 + Heartbeat + Cron）

---

## 快速测试

### 1. CLI 测试模式（最快）

```bash
# Windows
uv run python -m agent.main --mode cli --testing

# Linux
.venv/bin/python -m agent.main --mode cli --testing
```

**特点：**
- ✅ 无需任何配置
- ✅ 无需 API Key
- ✅ 命令行直接对话
- ✅ 适合验证系统是否正常

---

## 生产使用

### 2. Gateway 模式（默认）⭐

```bash
# 默认启动（自动启用所有功能）
uv run python -m agent.main

# 完整命令（显式指定所有参数）
uv run python -m agent.main \
  --mode gateway \
  --enable-feishu \
  --enable-heartbeat \
  --enable-cron \
  --heartbeat-interval 30 \
  --log-level INFO
```

**包含功能：**
- ✅ 飞书 WebSocket 长连接
- ✅ Heartbeat（每 30 分钟检查任务清单）
- ✅ Cron（精确时间执行任务）
- ✅ 日志记录到 `workspace/logs/`
- ✅ Metrics 和 Tracing

**环境变量要求：**

```bash
# Linux
export FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
export OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx

# Windows PowerShell
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
$env:OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
```

---

## 开发调试

### 3. Web UI 模式

```bash
# 测试模式（无需 API Key）
uv run python -m agent.main --mode web --testing

# 真实模式
uv run python -m agent.main --mode web

# 自定义端口
uv run python -m agent.main --mode web --web-port 9000
```

**访问：** http://127.0.0.1:8000

**特点：**
- ✅ 可视化界面
- ✅ 支持 interrupt/approval
- ✅ Thread 历史回放
- ✅ 工具调用流式展示

---

## 全功能模式

### 4. All 模式（Web + Gateway 同时）

```bash
uv run python -m agent.main --mode all
```

**包含功能：**
- ✅ Web UI（浏览器访问）
- ✅ 飞书通道
- ✅ Heartbeat + Cron
- ✅ 所有观测性功能

**适用场景：**
- 开发环境
- 需要同时用飞书和 Web UI

---

## 自定义配置

### 5. 调整 Heartbeat 间隔

```bash
# 每 10 分钟检查一次（频繁）
uv run python -m agent.main --heartbeat-interval 10

# 每 60 分钟检查一次（低频）
uv run python -m agent.main --heartbeat-interval 60

# 禁用 Heartbeat
uv run python -m agent.main --no-heartbeat
```

### 6. 调整日志级别

```bash
# 调试模式（详细日志）
uv run python -m agent.main --log-level DEBUG

# 仅错误日志
uv run python -m agent.main --log-level ERROR
```

### 7. 禁用某些功能

```bash
# 仅飞书，不启用 Heartbeat 和 Cron
uv run python -m agent.main --no-heartbeat --no-cron

# 不启用飞书（仅 Heartbeat + Cron，用于定时任务）
uv run python -m agent.main --no-feishu
```

---

## Linux systemd 服务

### 8. 作为系统服务运行

**创建 service 文件：**

```bash
sudo nano /etc/systemd/system/yiminai.service
```

**内容：**

```ini
[Unit]
Description=Yi Min AI Agent
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/opt/yi-min-ai
EnvironmentFile=/opt/yi-min-ai/.env
ExecStart=/opt/yi-min-ai/.venv/bin/python -m agent.main
Restart=always

[Install]
WantedBy=multi-user.target
```

**启用服务：**

```bash
sudo systemctl daemon-reload
sudo systemctl enable yiminai
sudo systemctl start yiminai

# 查看状态
sudo systemctl status yiminai

# 查看日志
sudo journalctl -u yiminai -f
```

---

## Windows 任务计划（开机自启）

### 9. 使用 Task Scheduler

1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：系统启动时
4. 操作：启动程序
   - 程序：`D:\dev\agent\yi-min-ai\.venv\Scripts\python.exe`
   - 参数：`-m agent.main`
   - 起始于：`D:\dev\agent\yi-min-ai`

**或使用 PowerShell 脚本：**

```powershell
# start_yiminai.ps1
cd D:\dev\agent\yi-min-ai
$env:FEISHU_APP_ID="cli_xxx"
$env:FEISHU_APP_SECRET="xxx"
$env:OPENAI_API_KEY="sk-xxx"
.venv\Scripts\python.exe -m agent.main
```

---

## Docker（可选）

### 10. 使用 Docker 运行

**Dockerfile：**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制项目文件
COPY . .

# 安装依赖
RUN uv sync

# 暴露端口（如果需要 Web UI）
EXPOSE 8000

# 设置环境变量（通过 docker run -e 传入）
ENV FEISHU_APP_ID=""
ENV FEISHU_APP_SECRET=""
ENV OPENAI_API_KEY=""

# 启动命令
CMD [".venv/bin/python", "-m", "agent.main"]
```

**构建并运行：**

```bash
# 构建镜像
docker build -t yiminai .

# 运行容器
docker run -d \
  --name yiminai \
  --restart always \
  -e FEISHU_APP_ID=cli_xxx \
  -e FEISHU_APP_SECRET=xxx \
  -e OPENAI_API_KEY=sk-xxx \
  -v $(pwd)/workspace:/app/workspace \
  yiminai

# 查看日志
docker logs -f yiminai
```

---

## 组合示例

### 11. 常见组合

```bash
# 1. 本地开发（Web UI + 测试模式）
uv run python -m agent.main --mode web --testing

# 2. 生产飞书（完整功能）
uv run python -m agent.main

# 3. 仅定时任务（不启用飞书）
uv run python -m agent.main --no-feishu

# 4. 高频 Heartbeat（每 5 分钟）
uv run python -m agent.main --heartbeat-interval 5

# 5. 调试模式（详细日志）
uv run python -m agent.main --log-level DEBUG

# 6. 全功能 + 自定义配置
uv run python -m agent.main \
  --mode all \
  --heartbeat-interval 15 \
  --web-port 9000 \
  --log-level INFO
```

---

## 查看帮助

```bash
# 查看所有可用参数
uv run python -m agent.main --help

# 输出示例：
# Usage: main.py [OPTIONS]
#
# Options:
#   --mode [cli|web|gateway|all]  启动模式（默认: gateway）
#   --config PATH                 配置文件路径
#   --testing / --no-testing      测试模式
#   --enable-feishu / --no-feishu 是否启用飞书
#   --enable-heartbeat / --no-heartbeat
#   --heartbeat-interval INTEGER  Heartbeat 间隔（分钟）
#   --enable-cron / --no-cron
#   --web-port INTEGER            Web UI 端口
#   --log-level [DEBUG|INFO|WARNING|ERROR]
#   --help                        显示此帮助信息
```

---

## 推荐配置总结

| 场景 | 命令 |
|------|------|
| **本地测试** | `python -m agent.main --mode cli --testing` |
| **生产飞书** | `python -m agent.main` |
| **开发调试** | `python -m agent.main --mode web --testing` |
| **全功能** | `python -m agent.main --mode all` |
| **Linux 服务** | systemd + `python -m agent.main` |

**最推荐：** 直接运行 `python -m agent.main`，自动启用所有功能！ ⭐
