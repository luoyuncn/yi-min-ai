# 飞书集成测试指南

## 前置准备

### 1. 创建飞书企业自建应用

1. 访问 [飞书开放平台](https://open.feishu.cn/app)
2. 点击"创建企业自建应用"
3. 填写应用名称和描述（例如："Yi Min AI Agent"）
4. 创建成功后，记录以下信息：
   - **App ID**（应用凭证页面）
   - **App Secret**（应用凭证页面）

### 2. 开启机器人能力

1. 在应用管理页面，点击"添加应用能力"
2. 选择"机器人"
3. 配置机器人信息：
   - 机器人名称
   - 描述
   - 头像

### 3. 配置权限

在"权限管理"页面，开通以下权限：

**必需权限：**
- `im:message` - 获取与发送单聊、群组消息
- `im:message:send_as_bot` - 以应用的身份发消息
- `im:message.receive` - 接收消息 v2.0（重要！）

**可选权限：**
- `im:chat` - 获取群组信息
- `contact:user.base` - 获取用户基本信息

### 4. 订阅事件

1. 进入"事件订阅"页面
2. 选择"使用长连接方式接收事件"（WebSocket 模式，推荐）
3. 订阅以下事件：
   - **接收消息 v2.0** (`im.message.receive_v1`)

**注意：** 使用长连接模式无需配置请求网址，本地开发即可接收消息。

### 5. 发布版本

1. 进入"版本管理与发布"
2. 创建版本并提交审核
3. 审核通过后，在"应用发布"中申请全员可用

---

## 本地测试步骤

### Step 1: 设置环境变量

在项目根目录创建或编辑 `.env` 文件：

```bash
# .env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：设置主 Provider 的 API Key
OPENAI_API_KEY=your-openai-key
# 或
ANTHROPIC_API_KEY=your-anthropic-key
```

**PowerShell 临时设置（推荐测试时使用）：**

```powershell
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
$env:OPENAI_API_KEY="your-openai-key"
```

### Step 2: 启动 Gateway 服务器

**仅启用飞书通道：**

```bash
uv run python -m agent.gateway.main
```

**启用飞书 + Heartbeat（每 30 分钟检查一次）：**

```bash
uv run python -m agent.gateway.main --enable-heartbeat
```

**启用飞书 + Heartbeat + Cron：**

```bash
uv run python -m agent.gateway.main --enable-heartbeat --enable-cron
```

**自定义配置：**

```bash
# Heartbeat 每 10 分钟检查一次
uv run python -m agent.gateway.main --enable-heartbeat --heartbeat-interval 10

# 调试模式（详细日志）
uv run python -m agent.gateway.main --log-level DEBUG
```

### Step 3: 添加机器人到群组或私聊

**私聊测试：**
1. 在飞书搜索框搜索你的机器人名称
2. 点击进入私聊
3. 发送消息："你好"

**群聊测试：**
1. 创建一个测试群组
2. 在群组设置中添加你的机器人
3. 在群里 @机器人 发送消息："@Yi Min AI 你好"

### Step 4: 验证功能

发送以下测试消息：

```
# 基础对话
你好，请介绍一下你自己

# 读取文件
读取 SOUL.md 的内容

# 会话检索
搜索我们昨天讨论的内容

# M-flow 深度检索（如果已启用）
recall_memory: 为什么我上周决定不用 Redis

# Web 搜索
web_search: Python async programming best practices

# 文件写入（会触发审批流）
file_write: 创建一个 test.md 文件，内容是"测试"
```

### Step 5: 查看日志

**实时查看 Gateway 日志：**

```bash
# PowerShell
Get-Content workspace\logs\gateway.log -Wait -Tail 20
```

**查看 Metrics：**

```bash
Get-Content workspace\metrics\metrics.jsonl | Select-Object -Last 10
```

**查看 Traces：**

```bash
$date = Get-Date -Format "yyyy-MM-dd"
Get-Content "workspace\traces\$date.jsonl" | Select-Object -Last 5
```

---

## 常见问题排查

### 1. "lark-oapi not installed"

**原因：** lark-oapi 依赖未安装

**解决：**
```bash
uv sync
```

### 2. "环境变量未设置"

**症状：** 启动时提示 `FEISHU_APP_ID` 或 `FEISHU_APP_SECRET` 未设置

**解决：**
```powershell
# 检查环境变量
$env:FEISHU_APP_ID
$env:FEISHU_APP_SECRET

# 如果为空，重新设置
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3. "收不到消息"

**检查清单：**
- [ ] 飞书应用已发布且全员可用
- [ ] 已订阅"接收消息 v2.0"事件
- [ ] 已开通 `im:message.receive` 权限
- [ ] Gateway 服务器正在运行
- [ ] 私聊：直接发送消息
- [ ] 群聊：必须 @机器人

**查看日志：**
```bash
# 检查是否收到 WebSocket 事件
Get-Content workspace\logs\gateway.log | Select-String "Feishu message"
```

### 4. "WebSocket 连接失败"

**可能原因：**
- App ID 或 App Secret 错误
- 网络问题

**解决：**
1. 检查 App ID 和 App Secret 是否正确
2. 检查网络连接
3. 查看 gateway.log 中的详细错误信息

### 5. "群聊中机器人不响应"

**原因：** 群聊中必须 @机器人

**正确用法：**
```
@Yi Min AI 你好
```

**错误用法：**
```
你好  ← 机器人不会响应
```

---

## 高级配置

### 1. 配置 Heartbeat 任务

编辑 `workspace/HEARTBEAT.md`：

```markdown
# Proactive Tasks

## Every Morning (8:00 UTC+8)
- 检查昨日会话中是否有未完成的待办事项，如有则推送提醒到飞书

## Every 2 Hours
- 检查 workspace/inbox/ 目录是否有新文件需要处理

## Daily Evening (18:00 UTC+8)
- 生成今日工作摘要并推送到飞书
```

### 2. 配置 Cron 定时任务

编辑 `workspace/CRON.yaml`：

```yaml
tasks:
  - name: "morning_briefing"
    description: "每日早晨简报（推送到飞书）"
    schedule: "0 8 * * *"  # 每天 8:00
    timezone: "Asia/Shanghai"
    action:
      type: "prompt"
      prompt: |
        请生成今日工作简报，包含：
        1. 今日日程
        2. 待办事项
        3. 昨日工作回顾
    output:
      channel: "feishu"
      session_id: "default"  # TODO: 替换为你的飞书会话 ID
    enabled: true

  - name: "weekly_summary"
    description: "每周五晚间周报"
    schedule: "0 18 * * 5"  # 每周五 18:00
    timezone: "Asia/Shanghai"
    action:
      type: "prompt"
      prompt: "生成本周工作总结"
    output:
      channel: "feishu"
      session_id: "default"
    enabled: false  # 暂时禁用
```

**注意：** `session_id: "default"` 需要替换为实际的飞书会话 ID（chat_id）。

**获取 chat_id 的方法：**
1. 在飞书中给机器人发送一条消息
2. 查看 `workspace/logs/gateway.log`
3. 找到类似 `session_id=oc_xxxxxxxxxxxxxxx` 的日志
4. 将这个 ID 复制到 CRON.yaml 中

### 3. 配置默认推送会话

如果你希望 Heartbeat 和 Cron 的输出推送到固定的飞书群组或私聊：

1. 获取目标会话的 `chat_id`（方法见上）
2. 在启动脚本中设置默认会话 ID：

```python
# agent/gateway/server.py 中修改
DEFAULT_FEISHU_SESSION = "oc_xxxxxxxxxxxxxxx"  # 替换为实际 chat_id
```

---

## 性能优化建议

### 1. 调整 Heartbeat 间隔

根据实际需求调整间隔：

- **轻度使用**：60 分钟（`--heartbeat-interval 60`）
- **中度使用**：30 分钟（默认）
- **重度使用**：10 分钟（`--heartbeat-interval 10`）

### 2. 限制 Cron 任务数量

建议 Cron 任务不超过 5 个，避免资源占用过高。

### 3. 启用观测性

定期检查 Metrics 和 Traces，优化性能瓶颈：

```bash
# 查看最近的 LLM 调用延迟
Get-Content workspace\metrics\metrics.jsonl | 
  Select-String "llm_call" | 
  Select-Object -Last 10
```

---

## 生产部署建议

### 1. 使用进程管理工具

**Windows (NSSM)：**
```bash
nssm install YiMinAI "D:\dev\agent\yi-min-ai\.venv\Scripts\python.exe" 
  "-m" "agent.gateway.main" 
  "--enable-heartbeat" 
  "--enable-cron"
```

**Linux (systemd)：**
```ini
# /etc/systemd/system/yiminai.service
[Unit]
Description=Yi Min AI Gateway
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/yi-min-ai
Environment="FEISHU_APP_ID=cli_xxx"
Environment="FEISHU_APP_SECRET=xxx"
ExecStart=/path/to/.venv/bin/python -m agent.gateway.main --enable-heartbeat --enable-cron
Restart=always

[Install]
WantedBy=multi-user.target
```

### 2. 日志轮转

配置日志轮转，避免日志文件过大：

```bash
# Linux (logrotate)
# /etc/logrotate.d/yiminai
/path/to/workspace/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

### 3. 监控告警

定期检查 Gateway 运行状态，设置告警：

```bash
# 检查进程是否运行
Get-Process python | Where-Object {$_.CommandLine -like "*agent.gateway.main*"}

# 检查最近的错误日志
Get-Content workspace\logs\gateway.log | Select-String "ERROR" | Select-Object -Last 5
```

---

## 下一步

完成测试后，你可以：

1. **日常使用**：将 Gateway 设置为开机自启
2. **自定义 Skill**：在 `workspace/skills/` 中添加领域专用技能
3. **扩展 MCP**：二期接入日历、邮件等外部服务
4. **数据分析**：定期查看 Metrics 和 Traces，优化 Agent 性能

---

## 参考资料

- [飞书开放平台文档](https://open.feishu.cn/document)
- [lark-oapi Python SDK](https://github.com/larksuite/oapi-sdk-python)
- [WebSocket 事件订阅](https://open.larksuite.com/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-guide)
