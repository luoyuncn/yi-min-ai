# 常见问题排查指南

## 启动问题

### 1. RuntimeError: asyncio.run() cannot be called from a running event loop

**症状：**
```
RuntimeError: asyncio.run() cannot be called from a running event loop
```

**原因：** 旧版本的 `build_app()` 在异步上下文中无法运行。

**解决：** 已在 v1.1 中修复。如果仍遇到此问题，请确保使用最新版本：

```bash
git pull
uv sync
```

---

### 2. 环境变量未设置

**症状：**
```
飞书通道需要设置环境变量：
  export FEISHU_APP_ID=your-app-id
  export FEISHU_APP_SECRET=your-app-secret
```

**解决方案 1: 使用 .env 文件（推荐）**

```bash
# 创建 .env 文件
cat > .env << 'EOF'
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
EOF

# 设置权限
chmod 600 .env
```

**解决方案 2: 手动设置环境变量**

```bash
# Linux/macOS
export FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
export OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx

# Windows PowerShell
$env:FEISHU_APP_ID="cli_xxxxxxxxxxxxxxx"
$env:FEISHU_APP_SECRET="xxxxxxxxxxxxxxxxxxxxxxxx"
$env:OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
```

**验证：**

```bash
# Linux/macOS
echo $FEISHU_APP_ID

# Windows PowerShell
$env:FEISHU_APP_ID
```

---

### 3. lark-oapi not installed

**症状：**
```
RuntimeError: lark-oapi not installed. Install with: pip install lark-oapi
```

**解决：**

```bash
# 同步所有依赖
uv sync

# 或单独安装
uv pip install lark-oapi
```

---

### 4. Provider API Key 缺失

**症状：**
```
No API key found for provider: anthropic
```

**解决：** 设置对应 Provider 的 API Key

```bash
# 如果使用 OpenAI
export OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx

# 如果使用 Anthropic
export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx
```

**检查当前使用的 Provider：**

```bash
# 查看 config/providers.yaml
cat config/providers.yaml | grep default_primary
```

---

## 飞书集成问题

### 5. 收不到消息

**检查清单：**

```bash
# 1. 检查应用是否发布
登录飞书开放平台 → 版本管理 → 确认已发布且全员可用

# 2. 检查事件订阅
开放平台 → 事件订阅 → 确认已订阅"接收消息 v2.0"

# 3. 检查权限
开放平台 → 权限管理 → 确认已开通 im:message.receive

# 4. 检查 Gateway 是否运行
ps aux | grep "agent.gateway.main"  # Linux
Get-Process python  # Windows

# 5. 查看日志
tail -f workspace/logs/gateway.log | grep -i feishu
```

**常见原因：**
- 应用未发布或未全员可用
- 未订阅"接收消息 v2.0"事件
- 未开通 `im:message.receive` 权限
- Gateway 未启动
- 群聊中未 @机器人

---

### 6. WebSocket 连接失败

**症状：**
```
✗ 飞书通道连接失败: ...
```

**排查步骤：**

```bash
# 1. 验证 App ID 和 Secret
echo "APP_ID: $FEISHU_APP_ID"
echo "APP_SECRET: $FEISHU_APP_SECRET"

# 2. 检查网络连接
curl -I https://open.feishu.cn

# 3. 查看详细错误
tail -100 workspace/logs/gateway.log | grep -A 10 "飞书"
```

**可能原因：**
- App ID 或 App Secret 错误
- 网络问题（防火墙/代理）
- 飞书服务暂时不可用

---

### 7. 群聊中机器人不响应

**原因：** 群聊中必须 @机器人才会响应

**正确用法：**
```
@机器人名称 你好
```

**错误用法：**
```
你好  ← 不会响应
机器人名称 你好  ← 不会响应
```

---

## 运行时问题

### 8. 内存占用过高

**诊断：**

```bash
# Linux
ps aux | grep python | awk '{print $6}'  # 查看内存使用 (KB)

# Windows
Get-Process python | Select-Object WorkingSet
```

**优化方案：**

1. **限制 Heartbeat 频率**
```bash
python -m agent.main --heartbeat-interval 60  # 改为 60 分钟
```

2. **定期清理 SQLite**
```bash
sqlite3 workspace/sessions.db "VACUUM;"
```

3. **systemd 资源限制**（Linux）
```ini
[Service]
MemoryLimit=2G
CPUQuota=200%
```

---

### 9. SQLite 数据库锁定

**症状：**
```
sqlite3.OperationalError: database is locked
```

**原因：** 多个进程同时访问数据库

**解决：**

```bash
# 1. 确认只有一个 Gateway 实例运行
ps aux | grep "agent.gateway.main"

# 2. 杀死重复进程
pkill -f "agent.gateway.main"

# 3. 重启服务
systemctl restart yiminai
```

---

### 10. 日志文件过大

**诊断：**

```bash
du -sh workspace/logs/*.log
```

**解决：** 配置日志轮转

```bash
# 创建 logrotate 配置
sudo nano /etc/logrotate.d/yiminai
```

**内容：**
```
/opt/yi-min-ai/workspace/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

**测试：**
```bash
sudo logrotate -d /etc/logrotate.d/yiminai
```

---

## 性能问题

### 11. LLM 调用延迟高

**诊断：**

```bash
# 查看最近的 Metrics
tail -50 workspace/metrics/metrics.jsonl | grep latency_ms
```

**优化方案：**

1. **检查网络延迟**
```bash
ping api.openai.com
```

2. **切换 Provider 或模型**
```yaml
# config/providers.yaml
providers:
  - name: "gpt-5"
    model: "gpt-5.4"  # 换成更快的模型
```

3. **检查 API Key 配额**
登录 Provider 控制台检查是否达到限流

---

### 12. Compaction 频繁触发

**症状：** 日志中频繁出现"Compacting context"

**原因：** 对话轮次过多，频繁触发上下文压缩

**优化：**

1. **调整压缩阈值**
```python
# agent/core/compaction.py
self.compaction_threshold = max_context_tokens - 8192  # 增加预留空间
```

2. **增加保留轮次**
```python
# agent/core/compaction.py
preserved_head = history[:8]   # 保留更多早期对话
preserved_tail = history[-16:]  # 保留更多最近对话
```

---

## 开发调试

### 13. 启用调试日志

```bash
# 详细日志
python -m agent.main --log-level DEBUG

# 查看特定模块的日志
tail -f workspace/logs/gateway.log | grep "agent.core.loop"
```

---

### 14. 手动测试 Provider

```bash
# 测试 OpenAI
.venv/bin/python << 'EOF'
import os
from openai import OpenAI
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}]
)
print(response.choices[0].message.content)
EOF

# 测试 Anthropic
.venv/bin/python << 'EOF'
import os
from anthropic import Anthropic
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
message = client.messages.create(
    model="claude-sonnet-4",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}]
)
print(message.content[0].text)
EOF
```

---

### 15. 重置数据库

**警告：** 此操作会删除所有会话历史！

```bash
# 备份
cp workspace/sessions.db workspace/sessions.db.backup

# 删除
rm workspace/sessions.db

# 重启服务（会自动创建新数据库）
systemctl restart yiminai
```

---

## 获取帮助

### 16. 查看系统状态

```bash
# Gateway 状态
systemctl status yiminai

# 最近的错误日志
journalctl -u yiminai -p err -n 50

# Metrics 汇总
tail -100 workspace/metrics/metrics.jsonl | jq -s '
  {
    llm_calls: [.[] | select(.type=="llm_call")] | length,
    success: [.[] | select(.type=="llm_call" and .data.success)] | length
  }
'
```

---

### 17. 收集诊断信息

```bash
# 创建诊断报告
cat > diagnostic_report.txt << EOF
=== System Info ===
Date: $(date)
OS: $(uname -a)
Python: $(python --version)

=== Environment ===
FEISHU_APP_ID: ${FEISHU_APP_ID:0:10}...
OPENAI_API_KEY: ${OPENAI_API_KEY:+SET}

=== Service Status ===
$(systemctl status yiminai 2>&1)

=== Recent Logs ===
$(journalctl -u yiminai -n 100 2>&1)

=== Metrics ===
$(tail -20 workspace/metrics/metrics.jsonl 2>&1)
EOF

cat diagnostic_report.txt
```

---

## 快速修复清单

| 问题 | 快速修复 |
|------|---------|
| 启动失败 | `git pull && uv sync` |
| 飞书收不到消息 | 检查权限、事件订阅、@机器人 |
| API Key 错误 | 检查 `.env` 文件或环境变量 |
| 内存占用高 | 增加 `--heartbeat-interval` |
| 日志过大 | 配置 `logrotate` |
| 数据库锁定 | 确保只有一个进程运行 |
| 调试模式 | `--log-level DEBUG` |

---

**如果问题仍未解决，请查看：**
- 日志文件：`workspace/logs/gateway.log`
- 系统日志：`journalctl -u yiminai -f`
- Metrics：`workspace/metrics/metrics.jsonl`
