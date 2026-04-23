# 飞书集成快速开始（5 分钟）

这是最快速的飞书集成测试指南，帮你在 5 分钟内完成首次对话。

## Step 1: 获取飞书凭证（2 分钟）

1. 访问 https://open.feishu.cn/app
2. 点击"创建企业自建应用"
3. 填写应用名称："Yi Min AI"
4. 进入应用管理页面：
   - **应用凭证** → 复制 `App ID` 和 `App Secret`
   - **添加应用能力** → 选择"机器人"
   - **权限管理** → 开通以下权限：
     - ✅ `im:message`
     - ✅ `im:message:send_as_bot`
     - ✅ `im:message.receive`（必需！）
   - **事件订阅** → 
     - 选择"使用长连接方式接收事件"
     - 订阅"接收消息 v2.0"
   - **版本管理与发布** → 创建版本 → 申请全员可用

## Step 2: 设置环境变量（1 分钟）

**PowerShell：**

```powershell
# 进入项目目录
cd D:\dev\agent\yi-min-ai

# 设置飞书凭证（替换为你的实际值）
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"

# 设置 LLM Provider API Key（选择一个）
$env:OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
# 或
$env:ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxxxxxx"
```

**或者编辑 `.env` 文件（推荐）：**

```bash
# .env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
```

## Step 3: 启动 Gateway 服务器（1 分钟）

```bash
uv run python -m agent.gateway.main
```

**预期输出：**

```
============================================================
Gateway 多通道服务器启动
============================================================
正在加载 Agent 应用...
✓ Agent 应用加载完成
正在连接飞书（APP_ID: cli_xxxxxx...）
✓ 飞书通道已连接
============================================================
Gateway 服务器运行中...
按 Ctrl+C 停止服务器
============================================================
```

## Step 4: 测试对话（1 分钟）

### 私聊测试

1. 在飞书搜索你的机器人名称（"Yi Min AI"）
2. 进入私聊窗口
3. 发送消息：`你好，请介绍一下你自己`
4. 等待回复（通常 2-5 秒）

### 群聊测试

1. 创建一个测试群组
2. 在群组设置中添加你的机器人
3. 在群里发送：`@Yi Min AI 你好`
4. 等待回复

### 测试消息示例

```
# 基础对话
你好

# 读取文件
读取 SOUL.md

# 搜索历史
搜索我们昨天讨论的内容

# Web 搜索
帮我搜索一下 Python async programming

# 写入文件（会触发审批）
创建一个 test.md 文件，内容是"测试成功"
```

---

## 常见问题

### ❌ "环境变量未设置"

**症状：** 启动时提示 `FEISHU_APP_ID` 未设置

**解决：**
```powershell
# 检查环境变量
$env:FEISHU_APP_ID
$env:FEISHU_APP_SECRET

# 如果为空，重新设置
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
```

### ❌ "lark-oapi not installed"

**解决：**
```bash
uv sync
```

### ❌ 收不到消息

**检查清单：**
- [ ] 应用已发布且全员可用
- [ ] 已订阅"接收消息 v2.0"事件
- [ ] 已开通 `im:message.receive` 权限
- [ ] Gateway 服务器正在运行
- [ ] 私聊：直接发送即可
- [ ] 群聊：必须 @机器人

### ❌ 群聊中不响应

**原因：** 群聊必须 @机器人

**正确：** `@Yi Min AI 你好`  
**错误：** `你好`（不会响应）

---

## 进阶配置

### 启用 Heartbeat（主动检查）

```bash
# 每 30 分钟检查一次任务清单
uv run python -m agent.gateway.main --enable-heartbeat
```

编辑 `workspace/HEARTBEAT.md` 配置任务：

```markdown
# Proactive Tasks

## Every Morning (8:00 UTC+8)
- 检查待办事项并推送到飞书

## Every 2 Hours
- 检查 inbox 目录是否有新文件
```

### 启用 Cron（定时任务）

```bash
# 启用 Heartbeat + Cron
uv run python -m agent.gateway.main --enable-heartbeat --enable-cron
```

编辑 `workspace/CRON.yaml` 配置定时任务：

```yaml
tasks:
  - name: "morning_briefing"
    schedule: "0 8 * * *"  # 每天 8:00
    action:
      type: "prompt"
      prompt: "生成今日工作简报"
    output:
      channel: "feishu"
      session_id: "oc_xxxxxxxxxxxxxxx"  # 替换为实际 chat_id
    enabled: true
```

**获取 chat_id：**
1. 给机器人发送一条消息
2. 查看日志：`Get-Content workspace\logs\gateway.log | Select-String "session_id"`
3. 复制 `oc_xxxxxxxxxxxxxxx` 到 CRON.yaml

---

## 下一步

✅ 测试成功后：

1. **生产部署**：参考 [docs/FEISHU_SETUP_GUIDE.md](docs/FEISHU_SETUP_GUIDE.md) 中的"生产部署建议"
2. **自定义 Skill**：在 `workspace/skills/` 中添加专属技能
3. **启用 M-flow**：配置长期记忆深度检索
4. **查看 Metrics**：定期检查 `workspace/metrics/` 优化性能

---

## 查看完整文档

- [完整飞书配置指南](docs/FEISHU_SETUP_GUIDE.md)
- [功能实现总结](docs/IMPLEMENTATION_SUMMARY.md)
- [验证清单](VERIFICATION_CHECKLIST.md)
- [更新日志](docs/CHANGELOG.md)

---

**祝你使用愉快！** 🎉

如有问题，请查看 `workspace/logs/gateway.log` 中的详细日志。
