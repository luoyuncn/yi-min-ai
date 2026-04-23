# 已知问题与解决方案

## AsyncIO 相关

### 1. lark-oapi WebSocket 事件循环警告

**症状：**
```
[Lark] [ERROR] connect failed, err: This event loop is already running
RuntimeWarning: coroutine 'Client._connect' was never awaited
```

**原因：** lark-oapi SDK 在后台线程中尝试创建事件循环时，检测到主线程已有运行中的循环。

**当前状态：** ⚠️ 警告可以忽略
- WebSocket 连接实际上是成功的
- 警告只是 SDK 内部的检测机制触发
- 不影响功能使用

**临时解决方案：**
```python
# agent/gateway/adapters/feishu.py
# SDK 在线程中会自动处理事件循环
# 虽然有警告，但连接正常
```

**长期解决方案（待 lark-oapi SDK 更新）：**
- 等待 SDK 支持传入外部事件循环
- 或使用 SDK 的异步模式（如果提供）

**验证功能正常：**
```bash
# 启动后查看日志
uv run python -m agent.main

# 预期输出（忽略警告）：
# ✓ 飞书通道已连接
# Gateway 服务器运行中...

# 测试发送消息到飞书机器人
# 应该能正常接收并回复
```

---

## M-flow 集成

### 2. M-flow SDK 未正式发布

**症状：**
```
M-flow SDK detected but not yet configured
M-flow not available, skipping initialization
```

**原因：** M-flow SDK 尚未正式发布到 PyPI

**当前状态：** ℹ️ 功能预留
- M-flow 集成代码已完成
- 等待 SDK 正式发布后启用
- 不影响其他功能使用

**解决方案：**
- 保持关注 M-flow 项目：https://github.com/FlowElement-ai/m_flow
- SDK 发布后安装：`uv pip install m_flow`
- 无需修改代码，自动启用

---

## Provider 相关

### 3. Provider API Key 环境变量

**症状：**
```
No API key found for provider: anthropic
```

**原因：** 未设置对应 Provider 的 API Key

**解决：**

方式 1：`.env` 文件（推荐）
```bash
# .env
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
# 或
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx
```

方式 2：环境变量
```bash
# Linux/macOS
export OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx

# Windows PowerShell
$env:OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxx"
```

**检查当前使用的 Provider：**
```bash
cat config/providers.yaml | grep default_primary
```

---

## 飞书集成

### 4. 群聊中机器人不响应

**症状：** 在群聊中发送消息，机器人没有任何反应

**原因：** 群聊模式下必须 @机器人才会响应

**正确用法：**
```
@机器人名称 你好
```

**错误用法：**
```
你好  ← 不会响应
机器人 你好  ← 不会响应
```

**代码逻辑：**
```python
# agent/gateway/adapters/feishu.py
if message.chat_type == "group":
    mentions = getattr(message, "mentions", [])
    if not any(m.get("id", {}).get("union_id") == self.app_id for m in mentions):
        return  # 群聊中未@机器人，忽略消息
```

---

### 5. 飞书应用未发布

**症状：** 启动正常，但收不到任何消息

**原因：** 飞书应用未发布或未全员可用

**解决步骤：**
1. 登录飞书开放平台
2. 进入应用管理 → 版本管理
3. 创建版本并提交审核
4. 审核通过后 → 应用发布 → 申请全员可用

---

### 6. 未订阅事件

**症状：** 启动正常，但收不到消息

**检查清单：**
- [ ] 事件订阅 → 选择"使用长连接方式"
- [ ] 订阅"接收消息 v2.0"（im.message.receive_v1）
- [ ] 权限管理 → 开通 im:message.receive

---

## 性能问题

### 7. SQLite 数据库锁定

**症状：**
```
sqlite3.OperationalError: database is locked
```

**原因：** 多个进程同时访问数据库

**解决：**
```bash
# 1. 确认只有一个 Gateway 实例运行
ps aux | grep "agent.main"  # Linux
Get-Process python  # Windows

# 2. 杀死重复进程
pkill -f "agent.main"  # Linux
Stop-Process -Name python  # Windows

# 3. 重启服务
sudo systemctl restart yiminai  # Linux
```

---

### 8. 内存占用持续增长

**症状：** 长时间运行后内存占用过高

**原因：** 对话历史未及时清理

**解决方案：**

1. **定期清理 SQLite**
```bash
# 添加到 crontab（每周执行）
0 3 * * 0 sqlite3 /opt/yi-min-ai/workspace/sessions.db "VACUUM;"
```

2. **限制 Heartbeat 频率**
```bash
python -m agent.main --heartbeat-interval 60  # 改为 60 分钟
```

3. **systemd 资源限制**
```ini
[Service]
MemoryLimit=2G
```

---

## Windows 特定问题

### 9. PowerShell 编码问题

**症状：** 日志输出乱码

**原因：** PowerShell 默认编码不是 UTF-8

**解决：**
```powershell
# 设置 PowerShell 编码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# 或使用 Windows Terminal（推荐）
```

---

### 10. 路径中的空格

**症状：** 启动失败，找不到文件

**原因：** 路径包含空格但未引用

**解决：**
```bash
# 错误
cd D:\My Projects\yi-min-ai

# 正确
cd "D:\My Projects\yi-min-ai"
```

---

## Linux 特定问题

### 11. systemd 服务无法启动

**症状：**
```
Failed to start yiminai.service: Unit yiminai.service not found
```

**检查：**
```bash
# 1. 检查 service 文件是否存在
ls -l /etc/systemd/system/yiminai.service

# 2. 检查文件权限
sudo chmod 644 /etc/systemd/system/yiminai.service

# 3. 重载 systemd
sudo systemctl daemon-reload

# 4. 查看详细错误
sudo journalctl -xe
```

---

### 12. Python 版本过低

**症状：**
```
This project requires Python >=3.12
```

**解决：**
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.12 python3.12-venv

# CentOS/RHEL
sudo yum install python312

# 验证
python3.12 --version
```

---

## 解决方案优先级

| 问题 | 严重程度 | 优先级 | 状态 |
|------|---------|-------|------|
| AsyncIO 警告 | 低 | P3 | ⚠️ 可忽略 |
| M-flow 未启用 | 中 | P2 | ℹ️ 等待 SDK |
| API Key 缺失 | 高 | P0 | ✅ 文档化 |
| 群聊不响应 | 中 | P2 | ✅ 已知行为 |
| 内存增长 | 中 | P2 | ✅ 有解决方案 |

---

## 报告新问题

如果遇到未列出的问题，请：

1. **查看日志**
```bash
tail -100 workspace/logs/gateway.log
```

2. **检查系统状态**
```bash
systemctl status yiminai  # Linux
Get-Process python  # Windows
```

3. **收集诊断信息**
```bash
# 参考 docs/TROUBLESHOOTING.md 中的"收集诊断信息"章节
```

4. **创建 Issue**
- 提供详细的错误信息
- 包含系统环境（OS、Python 版本）
- 附上相关日志

---

**最后更新：** 2026-04-23  
**版本：** v1.1.1
