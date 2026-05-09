# Proactive Agent Design

**Date:** 2026-05-09  
**Status:** Approved

## Overview

让 agent 拥有主动性——它可以在随机间隔唤醒，自由探索（搜索、回顾用户状态、思考），完成后自己决定是否值得发消息给用户。不是定时任务，是有自主意志的空闲时间。

## Goals

- Agent 以随机间隔自动唤醒，感觉不机械
- 唤醒后使用完整的自身能力（同款 SOUL.md 人格、全量工具、完整记忆）
- 做完事情之后，自己判断"值不值得打扰用户"
- 没话找话时安静退出，不强发
- 频率感知：把今日已发次数注入上下文，让 agent 自己把控节奏

## Non-Goals

- 不建立新的 agent 实例或独立人格
- 不硬性限制每日发送次数（软暗示，不硬拦截）
- 不支持多用户广播（当前只针对单一 session_id）

## Architecture

```
随机唤醒
    │
    ▼
ProactiveScheduler
  - random.uniform(min_interval, max_interval) sleep
  - 跳过 quiet_hours（如凌晨 0-7 点）
  - 查询今日已发次数（SessionArchive）
  - 构造 "自由时间" 触发消息
    │
    ▼
AgentCore.run(trigger_message)
  - 相同 SOUL.md 人格
  - 相同记忆系统（mem0 / SQLite FTS5）
  - sender="proactive" → 跳过 domain router → 全量工具
  - ReAct loop 正常跑，agent 自由探索
    │
    ▼
读取最终文本输出
  ├─ 包含 [不打扰] 或为空 → 静默退出
  └─ 有内容 → gateway.send_to_channel() → 发给用户
                           + 今日计数 +1
```

## Trigger Message Format

```
[自由时间] 现在是 {weekday} {HH:MM}，你有一些空闲。
今天已经主动联系过用户 {n} 次。

做任何你想做的事——搜索感兴趣的内容、回顾用户最近的状态、
或者只是想想有没有什么值得分享的。

完成之后：
- 如果有值得告诉用户的，直接写出来
- 如果没有什么要说的，回复：[不打扰]
```

## Tool Visibility

当前 domain router 将消息分为 7 个域。proactive 触发需要全量工具。

在 `agent/core/context.py` 里增加 sender 判断，跳过 domain router：

```python
if message.sender == "proactive":
    domain = "proactive"  # 使用 always + 所有非 admin 工具
```

全量工具指：所有 `always` + 各 domain 工具，但排除需要用户确认的工具（`shell_exec`、`file_write`、`profile_core_update`、`profile_write`、`assistant_identity_update`）。不修改 domain router 本身逻辑。

## Decision Signal

ProactiveScheduler 读取 AgentCore.run() 的最终文本输出：

```python
response = await core.run(trigger_message)

if not response or "[不打扰]" in response:
    return  # 静默退出

await gateway.send_to_channel(
    channel=config.channel,
    session_id=config.session_id,
    content=response
)
daily_counter.increment()
```

## Daily Send Counter

不建新表。查询 SessionArchive 中今天 `sender="proactive"` 的已归档消息数，注入触发消息。这是软性暗示，agent 自己决定是否继续发。

## Configuration

```yaml
proactive:
  enabled: true
  min_interval_minutes: 20
  max_interval_minutes: 90
  quiet_hours: [0, 1, 2, 3, 4, 5, 6, 7]  # 静默时段，跳过唤醒
  session_id: ""       # 发给哪个飞书会话
  channel: "feishu"
```

## Files Changed

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `agent/scheduler/proactive.py` | 新建 | ProactiveScheduler 实现 |
| `agent/gateway/main.py` | 修改 | 启动 ProactiveScheduler |
| `agent/core/context.py` | 修改 | 增加 proactive 工具域判断 |
| `config/agent.yaml` | 修改 | 增加 proactive 配置块 |

## Key Constraints

- ProactiveScheduler 只在 gateway 模式下运行（需要 Feishu 连接）
- quiet_hours 检测：唤醒后立即判断当前小时，在静默时段则 sleep 到下一个非静默时段
- AgentCore.run() 已返回最终纯文本，ProactiveScheduler 直接读取，无需额外处理
