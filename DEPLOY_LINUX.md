# Linux 生产部署指南

适用于 Ubuntu/Debian/CentOS 等 Linux 发行版。

## 环境准备

### 1. 系统要求

```bash
# 检查 Python 版本（需要 3.12+）
python3 --version

# 如果版本低于 3.12，需要升级
# Ubuntu/Debian:
sudo apt update
sudo apt install python3.12 python3.12-venv

# CentOS/RHEL:
sudo yum install python3.12
```

### 2. 安装 uv

```bash
# 官方推荐安装方式
curl -LsSf https://astral.sh/uv/install.sh | sh

# 或使用 pip
pip3 install uv

# 验证安装
uv --version
```

### 3. 克隆项目（或上传）

```bash
# 方式 1: Git 克隆
cd /opt
git clone <your-repo-url> yi-min-ai
cd yi-min-ai

# 方式 2: 从 Windows 上传
# 使用 scp 或 SFTP 上传整个项目目录
# scp -r D:\dev\agent\yi-min-ai user@server:/opt/
```

### 4. 安装依赖

```bash
cd /opt/yi-min-ai

# 同步依赖
uv sync

# 验证虚拟环境
source .venv/bin/activate
python --version
pip list
```

---

## 配置环境变量

### 方式 1: .env 文件（推荐）

```bash
# 创建 .env 文件
cat > .env << 'EOF'
# 飞书凭证
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx

# LLM Provider（选择一个）
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
# 或
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx

# 可选：自定义配置
# HEARTBEAT_INTERVAL=30
# LOG_LEVEL=INFO
EOF

# 设置权限（保护敏感信息）
chmod 600 .env
```

### 方式 2: systemd 环境变量

```bash
# 编辑 systemd service 文件时直接配置（见下文）
```

---

## 使用 systemd 管理服务（推荐）

### 1. 创建 systemd service 文件

```bash
sudo nano /etc/systemd/system/yiminai.service
```

**内容：**

```ini
[Unit]
Description=Yi Min AI Agent Gateway
After=network.target

[Service]
Type=simple
User=your-username
Group=your-group
WorkingDirectory=/opt/yi-min-ai

# 环境变量（从 .env 文件加载）
EnvironmentFile=/opt/yi-min-ai/.env

# 或直接在这里设置
# Environment="FEISHU_APP_ID=cli_xxx"
# Environment="FEISHU_APP_SECRET=xxx"
# Environment="OPENAI_API_KEY=sk-xxx"

# 启动命令（统一入口，默认模式）
ExecStart=/opt/yi-min-ai/.venv/bin/python -m agent.main

# 或自定义参数
# ExecStart=/opt/yi-min-ai/.venv/bin/python -m agent.main \
#   --mode gateway \
#   --enable-heartbeat \
#   --enable-cron \
#   --heartbeat-interval 30 \
#   --log-level INFO

# 重启策略
Restart=always
RestartSec=10

# 资源限制（可选）
# MemoryLimit=2G
# CPUQuota=200%

# 日志配置
StandardOutput=journal
StandardError=journal
SyslogIdentifier=yiminai

[Install]
WantedBy=multi-user.target
```

**说明：**
- `User` 和 `Group` 替换为实际的用户和组
- `EnvironmentFile` 从 `.env` 文件加载环境变量
- `ExecStart` 使用统一启动命令 `python -m agent.main`

### 2. 启用并启动服务

```bash
# 重载 systemd 配置
sudo systemctl daemon-reload

# 启用服务（开机自启）
sudo systemctl enable yiminai

# 启动服务
sudo systemctl start yiminai

# 查看状态
sudo systemctl status yiminai

# 查看日志
sudo journalctl -u yiminai -f

# 查看最近 100 行日志
sudo journalctl -u yiminai -n 100
```

### 3. 管理服务

```bash
# 停止服务
sudo systemctl stop yiminai

# 重启服务
sudo systemctl restart yiminai

# 重新加载配置（修改 .env 后）
sudo systemctl restart yiminai

# 查看服务是否自启
sudo systemctl is-enabled yiminai

# 禁用自启
sudo systemctl disable yiminai
```

---

## 日志管理

### 1. 应用日志

**位置：** `/opt/yi-min-ai/workspace/logs/`

```bash
# 实时查看 Gateway 日志
tail -f /opt/yi-min-ai/workspace/logs/gateway.log

# 或使用应用日志
tail -f /opt/yi-min-ai/workspace/logs/agent.log

# 查看 Metrics
tail -f /opt/yi-min-ai/workspace/metrics/metrics.jsonl

# 查看 Traces
tail -f /opt/yi-min-ai/workspace/traces/$(date +%Y-%m-%d).jsonl
```

### 2. systemd 日志

```bash
# 实时查看
sudo journalctl -u yiminai -f

# 查看指定时间范围
sudo journalctl -u yiminai --since "2026-04-23 08:00:00"

# 查看错误日志
sudo journalctl -u yiminai -p err

# 导出日志到文件
sudo journalctl -u yiminai > yiminai.log
```

### 3. 日志轮转（logrotate）

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
    delaycompress
    missingok
    notifempty
    create 0640 your-username your-group
    sharedscripts
    postrotate
        # 通知应用重新打开日志文件（如果需要）
        # systemctl reload yiminai
    endscript
}
```

**测试配置：**

```bash
sudo logrotate -d /etc/logrotate.d/yiminai
```

---

## 监控与告警

### 1. 健康检查脚本

```bash
# 创建健康检查脚本
nano /opt/yi-min-ai/healthcheck.sh
```

**内容：**

```bash
#!/bin/bash

# 检查服务是否运行
if systemctl is-active --quiet yiminai; then
    echo "✓ Service is running"
else
    echo "✗ Service is down"
    # 发送告警（例如通过邮件或飞书）
    # curl -X POST <webhook-url> -d "Yi Min AI service is down"
    exit 1
fi

# 检查最近的错误日志
ERROR_COUNT=$(journalctl -u yiminai --since "5 minutes ago" -p err | wc -l)
if [ $ERROR_COUNT -gt 10 ]; then
    echo "✗ Too many errors: $ERROR_COUNT"
    exit 1
fi

echo "✓ Health check passed"
```

**设置权限并测试：**

```bash
chmod +x /opt/yi-min-ai/healthcheck.sh
./healthcheck.sh
```

### 2. 定时健康检查（cron）

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每 5 分钟检查一次）
*/5 * * * * /opt/yi-min-ai/healthcheck.sh >> /var/log/yiminai-health.log 2>&1
```

### 3. 监控 Metrics

```bash
# 创建 Metrics 汇总脚本
nano /opt/yi-min-ai/metrics_summary.sh
```

**内容：**

```bash
#!/bin/bash

METRICS_FILE="/opt/yi-min-ai/workspace/metrics/metrics.jsonl"

if [ ! -f "$METRICS_FILE" ]; then
    echo "Metrics file not found"
    exit 1
fi

echo "=== Yi Min AI Metrics Summary ==="
echo "Date: $(date)"
echo ""

# 统计 LLM 调用次数
LLM_CALLS=$(grep -c '"type":"llm_call"' "$METRICS_FILE")
echo "LLM Calls: $LLM_CALLS"

# 统计工具调用次数
TOOL_CALLS=$(grep -c '"type":"tool_call"' "$METRICS_FILE")
echo "Tool Calls: $TOOL_CALLS"

# 统计成功率
SUCCESS=$(grep '"type":"llm_call"' "$METRICS_FILE" | grep -c '"success":true')
TOTAL=$LLM_CALLS
if [ $TOTAL -gt 0 ]; then
    SUCCESS_RATE=$(awk "BEGIN {printf \"%.2f\", ($SUCCESS/$TOTAL)*100}")
    echo "Success Rate: $SUCCESS_RATE%"
fi

echo ""
echo "Latest metrics:"
tail -5 "$METRICS_FILE"
```

```bash
chmod +x /opt/yi-min-ai/metrics_summary.sh

# 每日生成汇总报告
crontab -e
# 添加：每天 23:00 生成汇总
0 23 * * * /opt/yi-min-ai/metrics_summary.sh >> /var/log/yiminai-metrics-daily.log
```

---

## 安全加固

### 1. 文件权限

```bash
# 限制敏感文件访问
chmod 600 /opt/yi-min-ai/.env
chmod 700 /opt/yi-min-ai/workspace

# 限制日志文件访问
chmod 640 /opt/yi-min-ai/workspace/logs/*.log
chown -R your-username:your-group /opt/yi-min-ai
```

### 2. 防火墙配置

```bash
# 如果使用 Web UI，开放端口
sudo ufw allow 8000/tcp

# 查看规则
sudo ufw status
```

### 3. SELinux（如果启用）

```bash
# CentOS/RHEL 检查 SELinux 状态
getenforce

# 如果是 Enforcing，需要设置 context
sudo semanage fcontext -a -t bin_t "/opt/yi-min-ai/.venv/bin/python"
sudo restorecon -v /opt/yi-min-ai/.venv/bin/python
```

---

## 更新与维护

### 1. 更新代码

```bash
cd /opt/yi-min-ai

# 拉取最新代码
git pull

# 更新依赖
uv sync

# 重启服务
sudo systemctl restart yiminai
```

### 2. 备份

```bash
# 备份配置和数据
tar -czf yiminai-backup-$(date +%Y%m%d).tar.gz \
  /opt/yi-min-ai/workspace \
  /opt/yi-min-ai/.env \
  /opt/yi-min-ai/config

# 定期备份（crontab）
0 2 * * * tar -czf /backup/yiminai-$(date +\%Y\%m\%d).tar.gz /opt/yi-min-ai/workspace
```

### 3. 恢复

```bash
# 解压备份
tar -xzf yiminai-backup-20260423.tar.gz -C /

# 重启服务
sudo systemctl restart yiminai
```

---

## 性能优化

### 1. 调整 Python 进程数（如果需要）

```bash
# 修改 systemd service 文件
sudo nano /etc/systemd/system/yiminai.service

# 添加资源限制
[Service]
# 限制内存使用（2GB）
MemoryLimit=2G

# 限制 CPU 使用（2 核）
CPUQuota=200%

# 重载配置
sudo systemctl daemon-reload
sudo systemctl restart yiminai
```

### 2. 优化数据库

```bash
# SQLite 定期 VACUUM（清理碎片）
sqlite3 /opt/yi-min-ai/workspace/sessions.db "VACUUM;"

# 添加到 cron（每周执行）
0 3 * * 0 sqlite3 /opt/yi-min-ai/workspace/sessions.db "VACUUM;"
```

---

## 故障排查

### 问题 1: 服务启动失败

```bash
# 查看详细错误
sudo journalctl -u yiminai -xe

# 检查配置文件
sudo systemctl status yiminai

# 手动启动测试
cd /opt/yi-min-ai
source .venv/bin/activate
python -m agent.main
```

### 问题 2: 飞书连接失败

```bash
# 检查环境变量
systemctl show yiminai --property=Environment

# 测试飞书凭证
python3 << 'EOF'
import os
print("APP_ID:", os.environ.get("FEISHU_APP_ID"))
print("APP_SECRET:", "***" if os.environ.get("FEISHU_APP_SECRET") else "NOT SET")
EOF

# 查看连接日志
tail -f workspace/logs/gateway.log | grep -i feishu
```

### 问题 3: 内存不足

```bash
# 查看内存使用
ps aux | grep python
free -h

# 限制内存（修改 service 文件）
MemoryLimit=2G
```

---

## 完整部署脚本（一键部署）

```bash
#!/bin/bash
# deploy.sh - Yi Min AI 一键部署脚本

set -e

echo "=== Yi Min AI Linux 部署脚本 ==="

# 1. 安装依赖
echo "步骤 1: 安装依赖..."
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 进入项目目录
cd /opt/yi-min-ai

# 3. 同步依赖
echo "步骤 2: 同步 Python 依赖..."
uv sync

# 4. 创建 .env 文件（如果不存在）
if [ ! -f .env ]; then
    echo "步骤 3: 创建 .env 文件..."
    cat > .env << 'EOF'
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
EOF
    chmod 600 .env
    echo "请编辑 .env 文件填入实际凭证："
    echo "  nano /opt/yi-min-ai/.env"
fi

# 5. 创建 systemd service
echo "步骤 4: 创建 systemd service..."
sudo tee /etc/systemd/system/yiminai.service > /dev/null << EOF
[Unit]
Description=Yi Min AI Agent Gateway
After=network.target

[Service]
Type=simple
User=$(whoami)
Group=$(id -gn)
WorkingDirectory=/opt/yi-min-ai
EnvironmentFile=/opt/yi-min-ai/.env
ExecStart=/opt/yi-min-ai/.venv/bin/python -m agent.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 6. 启用服务
echo "步骤 5: 启用并启动服务..."
sudo systemctl daemon-reload
sudo systemctl enable yiminai

echo ""
echo "=== 部署完成！==="
echo ""
echo "下一步："
echo "  1. 编辑 .env 文件：nano /opt/yi-min-ai/.env"
echo "  2. 启动服务：sudo systemctl start yiminai"
echo "  3. 查看状态：sudo systemctl status yiminai"
echo "  4. 查看日志：sudo journalctl -u yiminai -f"
```

**使用：**

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

---

## 总结

**推荐部署方式（生产）：**

1. **统一启动命令**：`python -m agent.main`（默认 gateway 模式）
2. **systemd 管理**：开机自启、自动重启
3. **日志轮转**：避免磁盘占满
4. **健康检查**：定时监控 + 告警
5. **定期备份**：保护数据安全

**一键启动（所有功能）：**

```bash
# 默认启动（Gateway + Heartbeat + Cron + 飞书）
python -m agent.main

# 自定义参数
python -m agent.main \
  --heartbeat-interval 10 \
  --log-level DEBUG
```

现在你可以在 Linux 上轻松部署和管理 Yi Min AI Agent 了！ 🚀
