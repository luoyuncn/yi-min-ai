# Yimin AI Agent 系统设计文档

> **文档版本**: v1.1  
> **日期**: 2026-04-22  
> **状态**: 一期设计 / 待评审  
> **变更说明**: v1.1 新增 LLM Provider 抽象层、观测性设计、Session 管理、Cron 定时任务

---

## 目录

- [1. 项目概述](#1-项目概述)
  - [1.1 目标](#11-目标)
  - [1.2 设计原则](#12-设计原则)
  - [1.3 一期范围](#13-一期范围)
- [2. 系统架构](#2-系统架构)
  - [2.1 总体架构图](#21-总体架构图)
  - [2.2 技术栈](#22-技术栈)
  - [2.3 目录结构](#23-目录结构)
- [3. 核心模块详细设计](#3-核心模块详细设计)
  - [3.1 Gateway 网关层](#31-模块一gateway-网关层)
  - [3.2 Agent Core 核心循环层](#32-模块二agent-core-核心循环层)
  - [3.3 Memory 记忆系统](#33-模块三memory-记忆系统)
  - [3.4 Tool & MCP 工具层](#34-模块四tool--mcp-工具层)
  - [3.5 Skill 系统](#35-模块五skill-系统)
  - [3.6 Heartbeat 与 Cron 调度](#36-模块六heartbeat-与-cron-调度)
  - [3.7 LLM Provider 抽象层](#37-模块七llm-provider-抽象层)
  - [3.8 Session 管理](#38-模块八session-管理)
  - [3.9 观测性系统](#39-模块九观测性系统)
- [4. 数据流全景](#4-数据流全景)
  - [4.1 消息完整生命周期](#41-一条消息的完整生命周期)
  - [4.2 M-flow 数据写入与检索流](#42-m-flow-数据写入与检索流)
- [5. 配置文件设计](#5-配置文件设计)
- [6. 安全设计](#6-安全设计)
- [7. 扩展预留（二期+）](#7-扩展预留二期)
- [8. 一期实施里程碑](#8-一期实施里程碑)
- [9. 关键设计决策记录](#9-关键设计决策记录)

---

## 1. 项目概述

### 1.1 目标

构建一个完全定制化的个人助理 Agent 系统。该系统以 ReAct 单 Agent 为核心架构，集成 M-flow 认知记忆引擎作为长期记忆层，预留 MCP 协议工具扩展口子（一期不接入外部 MCP Server），通过 Skill 按需加载获得领域能力，支持主动调度，具备清晰的向 Sub-agent 和 Plan-and-Execute 模式扩展的路径。

### 1.2 设计原则

**Harness-First 架构思维。** 整个系统围绕 `Agent = Model + Harness` 这一范式设计。Harness 是指模型之外的一切：工具层、记忆系统、安全护栏、上下文管理、反馈回路。它分为两大控制机制——Guides（前馈控制）在 Agent 行动前引导方向，Sensors（反馈控制）在 Agent 行动后观测并支持自我纠正。人类通过持续迭代 Guides 和 Sensors 来驯服 Agent。

**自研核心循环，不依赖重型框架。** 不使用 LangChain、LangGraph、CrewAI 等框架。核心 ReAct 循环、上下文组装、记忆管理均自研实现，确保对每一层的完全理解和控制。框架的抽象层对个人助理这一单 Agent 场景是负担而非助力。

**极简基础设施。** 本地运行、Markdown 文件做配置和人格、M-flow 使用 LanceDB 嵌入模式（零外部依赖），除 M-flow 外不引入额外数据库。所有状态可通过文件系统直接审查和编辑。

**面向扩展设计。** 一期虽为 ReAct 单 Agent，但所有模块接口为未来的 Sub-agent（作为 tool 的隔离子进程）和 Plan-and-Execute（Planner 作为 tool）预留扩展点。MCP 协议一期做好 Client 框架和工具注册机制，后续接入外部 Server 只需添加配置。

### 1.3 一期范围

**一期交付**：一个功能完整、可日常使用的个人助理 Agent，具备以下能力：

- ReAct 推理-行动核心循环
- 基于 M-flow 的图路由长期记忆（Cone Graph + LanceDB）
- 内置工具集（文件读写、Shell 执行、Web 搜索）
- MCP Client 框架 + 工具注册机制（预留口子，不接外部 Server）
- Skill 按需加载系统
- Heartbeat 主动调度 + Cron 定时任务
- CLI + 飞书 双通道接入
- **[v1.1 新增]** 多 LLM Provider 支持（Anthropic/OpenAI/OpenAI 兼容服务）
- **[v1.1 新增]** 观测性系统（Metrics/Tracing/Logging）
- **[v1.1 新增]** Session 生命周期管理

**一期明确不包含**：

- 外部 MCP Server 接入（日历/邮件/Notion 等，放二期）
- Sub-agent 委派
- Plan-and-Execute 规划器
- Learning Loop 自动生成 Skill
- 多 Agent 路由

---

## 2. 系统架构

### 2.1 总体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                        人类操控者                                  │
│                 持续迭代 Guides（前馈）和 Sensors（反馈）            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Layer 1: Gateway 网关层                        │
│                                                                  │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                   │
│   │ CLI      │    │ 飞书      │    │ (预留)    │                   │
│   │ Adapter  │    │ Adapter  │    │ Web/其他  │                   │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘                   │
│        └───────────────┼───────────────┘                         │
│                        ▼                                         │
│              ┌──────────────────┐                                │
│              │ Message Normalizer│                                │
│              │ (统一消息格式)     │                                │
│              └────────┬─────────┘                                │
│                       ▼                                          │
│              ┌──────────────────┐                                │
│              │ Session Manager  │  ← [v1.1 新增] 生命周期管理      │
│              │ + Command Queue  │                                │
│              │ (串行执行保证)    │                                │
│              └────────┬─────────┘                                │
└───────────────────────┼──────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│                 Layer 2: Agent Core 核心循环层                     │
│                                                                  │
│   ┌──────────────────────────────────────────────────────┐       │
│   │              Context Assembly Engine                  │       │
│   │                                                      │       │
│   │  ┌────────┐ ┌──────────┐ ┌───────────┐ ┌────────┐  │       │
│   │  │System  │ │SOUL.md + │ │Skill Index│ │Session │  │       │
│   │  │Prompt  │ │MEMORY.md │ │(名称+摘要) │ │History │  │       │
│   │  └────────┘ └──────────┘ └───────────┘ └────────┘  │       │
│   └──────────────────────┬───────────────────────────────┘       │
│                          ▼                                       │
│   ┌──────────────────────────────────────────────────────┐       │
│   │              ReAct Loop (核心引擎)                     │       │
│   │                                                      │       │
│   │   loop:                                              │       │
│   │     preflight_check(context) → 压缩/compaction       │       │
│   │     response = ProviderManager.call(context)         │       │
│   │     if text_response → stream_reply(), break         │       │
│   │     if tool_call → result = execute(tool), append    │       │
│   └──────────────────────┬───────────────────────────────┘       │
│                          │                                       │
│   ┌──────────────────────▼───────────────────────────────┐       │
│   │         Session Persistence & Compaction              │       │
│   │  每轮结束 → SQLite 归档 + M-flow 增量入库              │       │
│   └──────────────────────────────────────────────────────┘       │
└───────────────────────────┬──────────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┬─────────────┐
              ▼             ▼             ▼             ▼
┌─────────────────┐ ┌──────────────┐ ┌───────────────────┐ ┌─────────────────┐
│ Layer 3: Tools  │ │Layer 4: Skill│ │ Layer 5: Memory   │ │Layer 6: Provider│
│                 │ │System        │ │ (M-flow + 文件)    │ │Manager [v1.1]   │
│ ┌─────────────┐ │ │              │ │                   │ │                 │
│ │ 内置工具     │ │ │ ~/.agent/    │ │ ┌───────────────┐ │ │ ┌─────────────┐ │
│ │ file_read   │ │ │ skills/      │ │ │ Always-On     │ │ │ │ Anthropic   │ │
│ │ file_write  │ │ │ ├─ SKILL.md  │ │ │ SOUL.md       │ │ │ │ Provider    │ │
│ │ shell_exec  │ │ │ ├─ refs/     │ │ │ MEMORY.md     │ │ │ └─────────────┘ │
│ │ web_search  │ │ │ └─ scripts/  │ │ └───────────────┘ │ │ ┌─────────────┐ │
│ └─────────────┘ │ │              │ │ ┌───────────────┐ │ │ │ OpenAI      │ │
│ ┌─────────────┐ │ │ 按需加载:    │ │ │ M-flow        │ │ │ │ Compatible  │ │
│ │ MCP 框架    │ │ │ 仅名称+摘要  │ │ │ Cone Graph    │ │ │ └─────────────┘ │
│ │ (一期预留)   │ │ │ 注入上下文;  │ │ │ (LanceDB)     │ │ │ ┌─────────────┐ │
│ │ Client +    │ │ │ 完整内容按需  │ │ │ Episode →     │ │ │ │ Fallback    │ │
│ │ Registry    │ │ │ 读取         │ │ │ Facet →       │ │ │ │ + Health    │ │
│ │ 不接外部    │ │ │              │ │ │ FacetPoint →  │ │ │ │ Check       │ │
│ │ Server      │ │ │              │ │ │ Entity        │ │ │ └─────────────┘ │
│ └─────────────┘ │ │              │ │ └───────────────┘ │ └─────────────────┘
└─────────────────┘ └──────────────┘ │ ┌───────────────┐ │
                                     │ │ Session       │ │
                                     │ │ Archive       │ │
                                     │ │ (SQLite+FTS5) │ │
                                     │ └───────────────┘ │
                                     └───────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────┐
│            Layer 7: Heartbeat + Cron 主动调度层                   │
│                                                                  │
│   ┌─────────────────────────┐  ┌─────────────────────────┐       │
│   │ Heartbeat (轮询)         │  │ Cron (精确时间) [v1.1]  │       │
│   │ 默认30min → HEARTBEAT.md│  │ Cron表达式 → CRON.yaml  │       │
│   │ → 检查是否有事做         │  │ → 精确时间点执行任务     │       │
│   └─────────────────────────┘  └─────────────────────────┘       │
└──────────────────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────┐
│            Layer 8: Observability 观测性层 [v1.1 新增]            │
│                                                                  │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│   │ Metrics     │  │ Tracing     │  │ Logging     │              │
│   │ token/延迟  │  │ 链路追踪     │  │ 结构化日志  │              │
│   │ 成本/成功率 │  │ 持久化JSONL │  │ 敏感脱敏    │              │
│   └─────────────┘  └─────────────┘  └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 类别 | 选型 | 理由 |
|------|------|------|
| **开发语言** | Python 3.12+ | M-flow 是 Python 生态；Agent 核心逻辑 Python 实现更灵活；LLM SDK 支持最好 |
| **LLM 调用** | Provider 抽象层（支持 Anthropic/OpenAI/兼容服务） | **[v1.1]** 统一接口 + 动态注册 + 自动 Fallback |
| **主模型** | Claude Sonnet（日常）/ Claude Opus（复杂任务） | Sonnet 性价比最优做主控；Opus 留给需要深度判断的场景 |
| **记忆-图路由** | M-flow + LanceDB（嵌入模式） | 零外部依赖、纯文件存储、图路由检索准确率领先（LoCoMo 81.8%）|
| **记忆-会话归档** | SQLite + FTS5 | 轻量、可靠、全文检索；与 M-flow 互补 |
| **配置与人格** | Markdown 文件 (SOUL.md / MEMORY.md / HEARTBEAT.md) | 人类可读可编辑、版本可控、透明审计 |
| **工具集成协议** | MCP（一期预留框架，不接外部 Server） | 标准化工具接口，一期做好 Client + Registry，二期按需扩展 |
| **消息通道-飞书** | `lark-oapi` 官方 Python SDK（WebSocket 长连接模式） | 官方 SDK 支持长连接事件订阅，无需公网 IP，本地开发即可接收消息 |
| **消息通道-CLI** | 标准 stdin/stdout | 开发调试主通道 |
| **进程管理** | systemd (Linux) / LaunchAgent (macOS) | Gateway 长驻后台运行 |
| **包管理** | uv | 快速、确定性依赖解析 |
| **观测性** | 自研 Metrics/Tracing/Logging | **[v1.1]** 轻量、无外部依赖、文件持久化 |

### 2.3 目录结构

```
my-agent/
├── agent/
│   ├── __init__.py
│   ├── core/
│   │   ├── loop.py              # ReAct 核心循环引擎
│   │   ├── context.py           # 上下文组装引擎
│   │   ├── compaction.py        # 上下文压缩/摘要
│   │   ├── provider.py          # LLM Provider 抽象基类 [v1.1]
│   │   └── provider_manager.py  # Provider 管理器 [v1.1]
│   ├── providers/               # [v1.1 新增] Provider 实现
│   │   ├── anthropic.py         # Anthropic Claude Provider
│   │   └── openai_compatible.py # OpenAI 兼容 Provider
│   ├── gateway/
│   │   ├── server.py            # Gateway 主进程
│   │   ├── router.py            # 会话路由 + Command Queue
│   │   ├── adapters/
│   │   │   ├── base.py          # ChannelAdapter 基类/协议
│   │   │   ├── cli.py           # CLI 通道适配器
│   │   │   └── feishu.py        # 飞书通道适配器
│   │   └── normalizer.py        # 消息标准化
│   ├── memory/
│   │   ├── always_on.py         # SOUL.md / MEMORY.md 读写
│   │   ├── session_archive.py   # SQLite 会话归档 + FTS5
│   │   ├── mflow_bridge.py      # M-flow 集成桥接层
│   │   └── compaction.py        # 记忆压缩策略
│   ├── session/                 # [v1.1 新增] Session 管理
│   │   ├── manager.py           # Session 生命周期管理
│   │   ├── models.py            # Session 数据模型
│   │   └── cleanup.py           # Session 清理调度
│   ├── tools/
│   │   ├── registry.py          # 统一工具注册表 (内置 + MCP)
│   │   ├── executor.py          # 工具执行引擎
│   │   ├── builtin/
│   │   │   ├── file_ops.py      # 文件读写
│   │   │   ├── shell.py         # Shell 执行 (带审批)
│   │   │   └── web_search.py    # Web 搜索
│   │   └── mcp/
│   │       ├── client.py        # MCP Client 实现 (一期预留)
│   │       └── discovery.py     # MCP Server 自动发现 (一期预留)
│   ├── skills/
│   │   ├── loader.py            # Skill 索引构建 + 按需加载
│   │   └── manager.py           # Skill CRUD 操作
│   ├── scheduler/
│   │   ├── heartbeat.py         # Heartbeat 定时触发
│   │   └── cron.py              # Cron 任务管理 [v1.1]
│   └── observability/           # [v1.1 新增] 观测性模块
│       ├── metrics.py           # 指标收集
│       ├── tracing.py           # 链路追踪
│       └── logging.py           # 结构化日志
├── workspace/                    # Agent 工作空间（运行时状态）
│   ├── SOUL.md                  # 人格定义
│   ├── MEMORY.md                # 长期事实记忆
│   ├── HEARTBEAT.md             # 主动任务清单
│   ├── CRON.yaml                # Cron 定时任务配置 [v1.1]
│   ├── skills/                  # Skill 文件目录
│   │   ├── daily-briefing/
│   │   │   └── SKILL.md
│   │   ├── email-triage/
│   │   │   └── SKILL.md
│   │   └── meeting-prep/
│   │       └── SKILL.md
│   ├── memory/                  # 每日日志目录
│   │   └── YYYY-MM-DD.md
│   ├── logs/                    # [v1.1] 日志目录
│   │   └── agent.log
│   ├── traces/                  # [v1.1] 链路追踪目录
│   │   └── YYYY-MM-DD.jsonl
│   ├── metrics/                 # [v1.1] 指标目录
│   │   └── metrics.json
│   └── sessions.db              # SQLite 会话归档
├── mflow_data/                   # M-flow 数据目录
│   └── lancedb/                 # LanceDB 嵌入式存储
├── config/
│   ├── agent.yaml               # 主配置文件
│   ├── mcp_servers.yaml         # MCP Server 配置 (一期为空，预留)
│   └── providers.yaml           # LLM Provider 配置 [v1.1]
├── tests/
├── pyproject.toml
└── README.md
```

---

## 3. 核心模块详细设计

### 3.1 模块一：Gateway 网关层

**职责**：作为系统的控制面，处理所有外部通信的接入、标准化、路由和执行调度。

**Gateway 进程**是一个长驻后台进程。所有通道适配器、Heartbeat 触发器、Cron 调度器都通过 Gateway 进入 Agent 核心循环。Gateway 不包含任何业务逻辑，它只负责"消息进来 → 交给 Agent Core → 结果出去"这一管道。

#### 3.1.1 统一消息格式

```python
@dataclass
class NormalizedMessage:
    message_id: str
    session_id: str
    sender: str
    body: str                        # 文本内容（语音/图片已预处理）
    attachments: list[Attachment]    # 图片/文件等附件
    channel: str                     # "cli" | "feishu" | ...
    metadata: dict                   # 通道特定元数据
    timestamp: datetime
```

#### 3.1.2 Channel Adapter 接口

每个通道实现统一的 `ChannelAdapter` 协议：

```python
class ChannelAdapter(Protocol):
    async def connect(self) -> None: ...
    async def receive(self) -> AsyncIterator[NormalizedMessage]: ...
    async def send(self, session_id: str, content: str) -> None: ...
    async def send_rich(self, session_id: str, blocks: list[ContentBlock]) -> None: ...
```

#### 3.1.3 飞书 Adapter 设计

飞书接入采用**官方 `lark-oapi` SDK 的 WebSocket 长连接模式**（而非传统 Webhook），核心优势是：无需公网 IP 或域名，本地开发环境即可接收消息；SDK 内置鉴权逻辑，建连后事件推送为明文，无需处理解密验签；无需部署防火墙和配置白名单。

**前置条件**：在飞书开放平台创建企业自建应用，开启机器人能力，订阅 `im.message.receive_v1`（接收消息 v2.0）事件，获取 APP_ID 和 APP_SECRET。

```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

class FeishuAdapter:
    """飞书通道适配器 - 基于 lark-oapi WebSocket 长连接"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._message_queue: asyncio.Queue[NormalizedMessage] = asyncio.Queue()
        self._lark_client: lark.Client = None

    async def connect(self) -> None:
        # 构建事件处理器
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

        # 建立 WebSocket 长连接（SDK 内置鉴权 + 重连）
        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # 构建 API Client 用于主动发消息
        self._lark_client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )

        # 在后台线程启动 WebSocket（SDK 的 start() 会阻塞）
        import threading
        threading.Thread(target=self._ws_client.start, daemon=True).start()

    def _on_message_receive(self, data: P2ImMessageReceiveV1) -> None:
        """飞书消息事件回调 → 标准化 → 放入队列"""
        event = data.event
        message = event.message

        # 仅处理文本消息（一期），后续扩展图片/文件/语音
        if message.message_type != "text":
            return

        import json
        content = json.loads(message.content)
        text = content.get("text", "")

        normalized = NormalizedMessage(
            message_id=message.message_id,
            session_id=message.chat_id,      # 以飞书会话 ID 作为 session
            sender=event.sender.sender_id.open_id,
            body=text,
            attachments=[],
            channel="feishu",
            metadata={
                "chat_type": message.chat_type,  # "p2p" | "group"
                "mentions": getattr(message, "mentions", []),
            },
            timestamp=datetime.fromtimestamp(int(message.create_time) / 1000),
        )
        self._message_queue.put_nowait(normalized)

    async def receive(self) -> AsyncIterator[NormalizedMessage]:
        while True:
            msg = await self._message_queue.get()
            yield msg

    async def send(self, session_id: str, content: str) -> None:
        """通过飞书 API 回复消息"""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(session_id)
                .msg_type("text")
                .content(json.dumps({"text": content}))
                .build()
            )
            .build()
        )
        self._lark_client.im.v1.message.create(request)

    async def send_rich(self, session_id: str, blocks: list[ContentBlock]) -> None:
        """发送富文本/消息卡片（飞书 Interactive Card）"""
        card_content = self._build_card(blocks)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(session_id)
                .msg_type("interactive")
                .content(json.dumps(card_content))
                .build()
            )
            .build()
        )
        self._lark_client.im.v1.message.create(request)
```

**飞书群聊 vs 私聊处理**：通过 `metadata.chat_type` 区分。群聊中仅响应 @机器人 的消息（通过 `mentions` 字段判断），私聊中响应所有消息。

#### 3.1.4 Command Queue（命令队列）

对同一 `session_id` 的消息严格串行执行。这是一个刻意的设计约束——并发执行同一会话的消息会导致工具冲突和会话历史不一致。不同 session 之间可以并发。

```python
class CommandQueue:
    """每个 session_id 一个 FIFO 队列，跨 session 并发"""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}

    async def enqueue(self, message: NormalizedMessage) -> None:
        sid = message.session_id
        if sid not in self._queues:
            self._queues[sid] = asyncio.Queue()
            self._workers[sid] = asyncio.create_task(self._process_lane(sid))
        await self._queues[sid].put(message)

    async def _process_lane(self, session_id: str) -> None:
        """单个 session 的串行处理循环"""
        while True:
            message = await self._queues[session_id].get()
            try:
                await self.agent_core.run(message)
            except Exception as e:
                logger.error(f"Session {session_id} error: {e}")
            finally:
                self._queues[session_id].task_done()
```

### 3.2 模块二：Agent Core 核心循环层

**职责**：实现 ReAct 推理-行动循环，这是整个系统的心脏。

#### 3.2.1 Context Assembly Engine（上下文组装引擎）

每次 Agent 处理消息前，组装引擎按固定顺序拼接上下文。系统提示词的前缀部分尽量保持稳定以利用 API Provider 的 Prompt Caching（Anthropic 的 cache 可节省约 90% 的重复前缀 token 费用）。

```
上下文组装顺序（从上到下拼接）:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[1] Base System Prompt          ← 固定不变，描述 Agent 身份和行为规范
[2] SOUL.md 内容                ← 人格/语气/沟通风格，低频变化
[3] MEMORY.md 内容              ← 长期事实记忆，低频变化
[4] Skill Index                 ← 所有 Skill 的名称+一行描述列表
[5] Tool Schemas                ← 所有可用工具的 JSON Schema
─── 以上为稳定前缀，可被 Prompt Cache 命中 ───
[6] Session History             ← 当前会话的对话历史（可能被压缩）
[7] Current Message             ← 用户本次发送的消息
```

**Token 预算管理**：组装前计算当前总 token 数。保留 `compaction_reserve`（默认 4096 tokens）确保模型有足够空间回复。即将超限时触发 Compaction。

```python
class ContextAssembly:
    def __init__(self, config: AgentConfig):
        self.max_context = config.model_context_limit
        self.compaction_reserve = 4096

    def assemble(self, session: Session, message: NormalizedMessage) -> list[Message]:
        context = []

        # [1-5] 稳定前缀
        system_prompt = self._build_system_prompt(
            base=self._load_base_prompt(),
            soul=self._load_file("workspace/SOUL.md"),
            memory=self._load_file("workspace/MEMORY.md"),
            skill_index=self.skill_loader.get_index(),
            tool_schemas=self.tool_registry.get_schemas(),
        )
        context.append(SystemMessage(content=system_prompt))

        # [6] 会话历史
        history = session.get_history()
        context.extend(history)

        # [7] 当前消息
        context.append(UserMessage(content=message.body))

        # 预算检查
        total_tokens = self._count_tokens(context)
        if total_tokens > self.max_context - self.compaction_reserve:
            context = self._compact(context)

        return context
```

#### 3.2.2 ReAct Loop（核心循环引擎）

整个系统最核心的代码：

```python
class AgentCore:
    async def run(self, message: NormalizedMessage) -> str:
        # [v1.1] 使用 Session Manager 获取或创建 Session
        session = await self.session_manager.get_or_create(
            message.session_id,
            channel=message.channel,
            chat_type=message.metadata.get("chat_type", "p2p"),
            sender=message.sender
        )
        context = self.context_assembly.assemble(session, message)

        # [v1.1] 开始链路追踪
        trace = tracer.start_trace(message.session_id, message.message_id)

        max_iterations = 25  # 安全上限，防止无限循环

        try:
            for iteration in range(max_iterations):
                # Pre-flight: 检查是否需要压缩
                context = self._preflight_compact(context)

                # [v1.1] 使用 Provider Manager 调用 LLM（带自动 Fallback）
                with trace.start_span(f"llm_call_{iteration}") as span:
                    response = await self.provider_manager.call(
                        LLMRequest(
                            messages=context,
                            tools=self.tool_registry.get_schemas(),
                            stream=True,
                        ),
                        role=ProviderRole.PRIMARY
                    )
                    span.set_attribute("provider", response.provider)
                    span.set_attribute("latency_ms", response.latency_ms)

                # [v1.1] 记录指标
                metrics.record_llm_call(
                    provider=response.provider,
                    success=True,
                    latency_ms=response.latency_ms,
                    input_tokens=response.usage.get("input_tokens", 0),
                    output_tokens=response.usage.get("output_tokens", 0),
                    cost_usd=self._calculate_cost(response)
                )

                # 分支 1: 文本回复 → 结束本轮
                if response.type == "text":
                    session.append(AssistantMessage(content=response.text))
                    await self._post_turn(session, message)
                    return response.text

                # 分支 2: 工具调用 → 执行并继续循环
                if response.type == "tool_calls":
                    session.append(AssistantMessage(tool_calls=response.tool_calls))

                    for tool_call in response.tool_calls:
                        with trace.start_span(f"tool_{tool_call.name}") as tool_span:
                            # Approval Flow: 高危工具需要人工确认
                            if self.tool_registry.requires_approval(tool_call.name):
                                approved = await self._request_approval(tool_call, message)
                                if not approved:
                                    result = ToolResult(error="User denied this action")
                                else:
                                    result = await self.tool_executor.execute(tool_call)
                            else:
                                result = await self.tool_executor.execute(tool_call)

                            tool_span.set_attribute("success", not result.error)
                            metrics.record_tool_call(tool_call.name, not result.error)

                        session.append(ToolMessage(
                            tool_call_id=tool_call.id,
                            content=result.to_string()
                        ))

                    context = self.context_assembly.rebuild(session, message)
                    continue

            # 安全兜底
            return "I've reached the maximum number of steps for this task..."

        finally:
            tracer.end_trace()
            metrics.record_message_processed()

    async def _post_turn(self, session: Session, message: NormalizedMessage):
        """每轮结束后的持久化操作"""
        # 1. 归档到 SQLite
        self.session_archive.persist(session)

        # 2. [v1.1] 通过 Session Manager 持久化
        await self.session_manager.persist(session)

        # 3. 增量写入 M-flow（异步，不阻塞响应）
        asyncio.create_task(
            self.mflow_bridge.ingest_turn(session.last_turn())
        )
```

#### 3.2.3 Context Compaction（上下文压缩）

当会话历史膨胀接近上下文窗口极限时，压缩引擎介入。策略：保留最早几轮和最近几轮的原始对话，中间部分用辅助小模型生成摘要替换。

```python
class CompactionEngine:
    async def compact(self, context: list[Message]) -> list[Message]:
        system_messages = [m for m in context if m.role == "system"]
        history = [m for m in context if m.role != "system"]

        # 保留最早 2 轮 + 最近 4 轮
        preserved_head = history[:4]   # 2 轮 = 4 条消息 (user+assistant)
        preserved_tail = history[-8:]  # 4 轮 = 8 条消息
        middle = history[4:-8]

        if not middle:
            return context

        # [v1.1] 使用 Provider Manager 调用压缩专用模型
        summary_response = await self.provider_manager.call(
            LLMRequest(
                messages=[
                    {"role": "system", "content": 
                        "Summarize this conversation preserving all key facts, "
                        "decisions, and action items. Be precise with names, dates, numbers."
                    },
                    {"role": "user", "content": self._format_messages(middle)}
                ],
                max_tokens=1024,
            ),
            role=ProviderRole.COMPACTION
        )

        # 重组上下文
        compacted = (
            system_messages
            + preserved_head
            + [SystemMessage(
                f"[Conversation summary of {len(middle)} messages]: {summary_response.text}"
            )]
            + preserved_tail
        )

        # 将 lineage（摘要→原始消息映射）写入 SQLite
        self.session_archive.store_lineage(
            summary_id=generate_id(),
            original_message_ids=[m.id for m in middle]
        )

        return compacted
```

### 3.3 模块三：Memory 记忆系统

记忆系统采用**三层架构**，每层有明确的职责边界、不同的读写时机、不同的存储后端。

```
┌──────────────────────────────────────────────────────────────┐
│                     Memory Architecture                      │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  Layer A: Always-On Prompt Memory                    │     │
│  │  (每次会话无条件注入)                                  │     │
│  │                                                     │     │
│  │  SOUL.md    — 人格、语气、沟通风格                     │     │
│  │  MEMORY.md  — 长期事实 (≤3500 字符硬上限)             │     │
│  │                                                     │     │
│  │  存储: Markdown 文件                                  │     │
│  │  读取: 每次上下文组装时                                │     │
│  │  写入: Agent 通过 memory_write tool；本轮修改下轮生效   │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  Layer B: Session Archive (情景记忆)                  │     │
│  │  (按需检索)                                           │     │
│  │                                                     │     │
│  │  存储: SQLite + FTS5 全文索引                         │     │
│  │  写入: 每轮对话结束后自动归档                          │     │
│  │  读取: Agent 通过 search_sessions tool 主动查询        │     │
│  │  作用: 回答"过去发生了什么"                             │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  Layer C: M-flow Cognitive Memory (认知记忆)          │     │
│  │  (按需检索 — 图路由深度检索)                           │     │
│  │                                                     │     │
│  │  存储: M-flow Cone Graph (LanceDB 嵌入模式)           │     │
│  │  写入: 每轮对话结束后异步增量入库                       │     │
│  │  读取: Agent 通过 recall_memory tool 主动查询          │     │
│  │  作用: 回答需要因果推理、跨会话关联、实体桥接的复杂问题  │     │
│  │                                                     │     │
│  │  Cone Graph 四层拓扑:                                 │     │
│  │    Episode    → 完整事件/决策过程 (锥底，最终返回单位)  │     │
│  │    Facet      → 事件的一个维度/切面                    │     │
│  │    FacetPoint → 原子事实断言 (锥尖，最精确匹配点)      │     │
│  │    Entity     → 命名实体，跨 Episode 桥接             │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

#### 3.3.1 Layer A: Always-On Prompt Memory

**SOUL.md** 定义 Agent 的身份：

```markdown
# Identity
你是我的个人助理，名字叫 Atlas。

# Communication Style
- 中文为主，技术术语保留英文
- 简洁直接，不要客套话
- 遇到不确定的事情主动说明，不要瞎编
- 涉及重要操作（发邮件、修改日程）前先确认

# Boundaries
- 不要在未经确认的情况下发送任何对外通信
- 日程冲突时优先保护"深度工作"时间段
```

**MEMORY.md** 存储 Agent 学到的关于你的长期事实。硬上限 3500 字符——这是设计约束，迫使 Agent 做策展而非堆积：

```markdown
# User Profile
- 技术栈: Python + TypeScript, 偏好 FastAPI, Next.js
- 工作时区: UTC+8
- 周五下午不安排会议
- 偏好异步沟通，非紧急事项用邮件不用电话

# Current Projects
- Project Alpha: AI Agent 助理系统开发，一期阶段
- Project Beta: 公司内部知识库重构，暂停中

# Preferences
- 日报格式: 按项目分组，每项不超过3行
```

Agent 通过三个操作管理：`add`（追加）、`replace`（替换）、`remove`（删除）。**关键约束：本轮修改下轮生效**，防止 Agent 单次会话内通过自我修改记忆导致行为漂移。

#### 3.3.2 Layer B: Session Archive

```sql
CREATE TABLE sessions (
    session_id TEXT,
    turn_index INTEGER,
    role TEXT,        -- "user" | "assistant" | "tool"
    content TEXT,
    tool_name TEXT,   -- nullable
    token_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, turn_index)
);

CREATE VIRTUAL TABLE sessions_fts USING fts5(
    content,
    content='sessions',
    content_rowid='rowid'
);

CREATE TABLE compaction_lineage (
    summary_id TEXT PRIMARY KEY,
    original_session_id TEXT,
    original_turn_start INTEGER,
    original_turn_end INTEGER,
    summary_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3.3.3 Layer C: M-flow Cognitive Memory

M-flow 不是简单的向量检索——它的图路由 Bundle Search 解决了传统 RAG 的根本问题：**相似度（similarity）≠ 相关性（relevance）**。

**为什么一期就引入 M-flow：** 记忆质量取决于从第一天开始的数据积累。M-flow 的 Cone Graph 需要足够的 Episode 数据才能发挥跨会话关联和实体桥接的优势。越早开始积累，后续记忆检索质量越高。如果先跑几个月再迁移，已积累数据需全量重新入库和构建图谱，成本远高于从一开始就并行写入。

**M-flow 集成架构**：

```
Agent Core
    │
    ├── 写入路径 (每轮结束后异步):
    │   └── mflow_bridge.ingest_turn(turn_data)
    │       └── M-flow Python SDK
    │           ├── await m_flow.add(conversation_text)
    │           └── await m_flow.memorize()
    │               ├── 文本分块 + 共指消解 (代词→实体名, 入库前完成)
    │               ├── 抽取 Episode / Facet / FacetPoint / Entity
    │               ├── 构建语义边 (边带自然语言描述，可被向量检索命中)
    │               ├── 所有节点和边文本向量化
    │               └── 写入 LanceDB (本地文件)
    │
    └── 读取路径 (Agent 主动调用 tool):
        └── recall_memory tool → m_flow.query(question, mode="EPISODIC")
            ├── Phase 1: 对 7 个向量集合并行搜索，找入口锚点
            ├── Phase 2: 锚点投射到知识图谱，展开一跳邻居
            ├── Phase 3: 路径代价从锥尖传播到锥底
            │   路径代价 = 起点向量距离 + 边向量距离×hop数 + miss惩罚
            │   Episode 得分 = 所有路径的最小代价 (一条强证据链即可)
            │   直接命中 Episode summary 的路径被额外惩罚 (防宽泛匹配)
            ├── Phase 4: 按 bundle cost 排序，取 top_k
            └── 返回完整 Episode bundle (含 Facet + Entity)
```

**M-flow Bridge 实现**：

```python
import m_flow

class MflowBridge:
    def __init__(self, data_dir: str = "mflow_data"):
        m_flow.configure(
            storage_path=data_dir,
            db_type="lancedb",
            llm_provider="anthropic",
            llm_model="claude-sonnet",
            embedding_model="text-embedding-3-small",
        )

    async def ingest_turn(self, turn: TurnData) -> None:
        """异步将一轮对话增量写入 M-flow"""
        formatted = self._format_turn(turn)
        try:
            await m_flow.add(data=formatted, dataset_name="conversations")
            await m_flow.memorize()
            metrics.record_mflow_operation("ingest")  # [v1.1]
        except Exception as e:
            logger.warning(f"M-flow ingestion failed (non-blocking): {e}")

    async def query(self, question: str, top_k: int = 5) -> list[EpisodeBundle]:
        """图路由检索"""
        metrics.record_mflow_operation("query")  # [v1.1]
        results = await m_flow.query(
            question=question,
            mode="EPISODIC",
            top_k=top_k,
            datasets=["conversations"]
        )
        return results.context

    def _format_turn(self, turn: TurnData) -> str:
        parts = [
            f"[{turn.timestamp.isoformat()}] Session: {turn.session_id}",
            f"User: {turn.user_message}",
        ]
        if turn.tool_calls:
            for tc in turn.tool_calls:
                parts.append(f"Tool({tc.name}): {tc.summary}")
        parts.append(f"Assistant: {turn.assistant_response}")
        return "\n".join(parts)
```

**Agent 可用的记忆相关 Tools**：

```python
@tool
def memory_write(action: str, key: str, value: str) -> str:
    """管理长期记忆 (MEMORY.md)。
    action: 'add' | 'replace' | 'remove'
    key: 记忆条目的标识
    value: 记忆内容（仅 add/replace 时需要）
    注意：修改在下一轮会话生效。"""

@tool
def search_sessions(query: str, limit: int = 5) -> str:
    """全文检索过去的会话记录。适用于查找具体事件、过去的对话内容。
    使用 SQLite FTS5，按关键词匹配。"""

@tool
def recall_memory(question: str, top_k: int = 3) -> str:
    """深度记忆检索（M-flow 图路由）。适用于需要因果推理、跨会话关联的复杂问题。
    例如："为什么上周我决定不用 Redis？""上次提到的那个性能问题后来怎样了？"
    返回完整的 Episode bundle，包含因果链。"""

@tool
def read_skill(skill_name: str) -> str:
    """按需读取某个 Skill 的完整内容。"""
```

**Session Archive vs M-flow 分工**：

| 维度 | Session Archive (SQLite FTS5) | M-flow (Cone Graph) |
|------|-------------------------------|---------------------|
| 检索方式 | 关键词全文匹配 | 图路由证据链传播 |
| 适用查询 | "上周三我跟你说了什么" | "为什么我最终选了方案A而不是B" |
| 粒度 | 完整对话轮次 | Episode bundle（结构化因果链）|
| 延迟 | <10ms | ~500ms-2s（含图传播） |
| 写入时机 | 每轮同步写入 | 每轮异步写入 |
| 数据组织 | 时间线（线性） | 知识拓扑（图） |

### 3.4 模块四：Tool & MCP 工具层

**职责**：给 Agent 提供与外部世界交互的能力。一期实现内置工具 + MCP 框架预留。

#### 3.4.1 统一工具注册表

所有工具（内置 + 未来的 MCP 工具）通过统一注册表管理，对 Agent Core 暴露一致接口：

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._approval_required: set[str] = set()

    def register(self, tool: ToolDefinition, requires_approval: bool = False):
        self._tools[tool.name] = tool
        if requires_approval:
            self._approval_required.add(tool.name)

    def get_schemas(self) -> list[dict]:
        """返回所有工具的 JSON Schema，供 LLM 使用"""
        return [tool.to_schema() for tool in self._tools.values()]

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self._approval_required
```

#### 3.4.2 一期内置工具

| 工具名 | 功能 | 需要审批 |
|--------|------|---------|
| `file_read` | 读取本地文件内容 | 否 |
| `file_write` | 写入/创建本地文件 | 否 |
| `shell_exec` | 执行 Shell 命令 | **是** |
| `web_search` | Web 搜索 | 否 |
| `memory_write` | 修改 MEMORY.md | 否 |
| `search_sessions` | SQLite FTS5 会话检索 | 否 |
| `recall_memory` | M-flow 图路由深度检索 | 否 |
| `read_skill` | 按需加载 Skill 完整内容 | 否 |

#### 3.4.3 MCP 框架预留（一期不接外部 Server）

一期实现 MCP Client 核心框架和工具自动发现机制，但 `config/mcp_servers.yaml` 保持为空。二期接入外部 MCP Server 时只需添加配置，无需改动任何代码。

**MCP Client 实现**：

```python
class MCPClient:
    """MCP 协议客户端 - 支持 stdio / sse / http 三种传输模式"""

    async def connect(self, server_config: MCPServerConfig) -> None:
        """根据配置建立与 MCP Server 的连接"""
        if server_config.transport == "stdio":
            self._transport = StdioTransport(
                command=server_config.command,
                args=server_config.args,
                env=server_config.env,
            )
        elif server_config.transport == "sse":
            self._transport = SSETransport(url=server_config.url)
        # ... http 等其他模式

        await self._transport.connect()

    async def list_tools(self) -> list[MCPToolDef]:
        """获取 Server 暴露的所有工具定义"""
        response = await self._transport.send("tools/list", {})
        return [MCPToolDef.from_dict(t) for t in response["tools"]]

    async def call_tool(self, tool_name: str, params: dict) -> str:
        """调用指定工具"""
        response = await self._transport.send("tools/call", {
            "name": tool_name,
            "arguments": params,
        })
        return response["content"]
```

**MCP 自动发现与注册**：

```python
class MCPDiscovery:
    """启动时自动发现所有配置的 MCP Server 并注册工具"""

    async def discover_and_register(self, registry: ToolRegistry):
        for server_config in self.config.mcp_servers:
            try:
                client = MCPClient()
                await client.connect(server_config)
                tools = await client.list_tools()

                for tool in tools:
                    registry.register(
                        ToolDefinition(
                            name=f"mcp_{server_config.name}_{tool.name}",
                            description=tool.description,
                            schema=tool.input_schema,
                            executor=lambda params, c=client, t=tool:
                                c.call_tool(t.name, params)
                        ),
                        requires_approval=server_config.get(
                            "requires_approval", False
                        )
                    )
                logger.info(
                    f"MCP: registered {len(tools)} tools "
                    f"from {server_config.name}"
                )
            except Exception as e:
                logger.warning(
                    f"MCP: failed to connect {server_config.name}: {e}"
                )
```

**MCP Server 配置文件**（一期为空，预留格式）：

```yaml
# config/mcp_servers.yaml
# 一期：不接入任何外部 MCP Server
# 二期示例配置（取消注释即可启用）：
#
# servers:
#   google_calendar:
#     transport: "stdio"
#     command: "npx"
#     args: ["-y", "@anthropic/mcp-google-calendar"]
#     env:
#       GOOGLE_CREDENTIALS_PATH: "~/.config/google/credentials.json"
#
#   gmail:
#     transport: "stdio"
#     command: "npx"
#     args: ["-y", "@anthropic/mcp-gmail"]
#     requires_approval: true
#
#   notion:
#     transport: "sse"
#     url: "http://localhost:3100/sse"
#
#   mflow_mcp:
#     transport: "stdio"
#     command: "python"
#     args: ["-m", "src.server", "--transport", "stdio"]
#     cwd: "./mflow-mcp"

servers: {}  # 一期为空
```

### 3.5 模块五：Skill 系统

**职责**：让 Agent 具备领域特定的专业能力，通过按需加载的 Markdown 指令文件实现。

#### 3.5.1 Skill 文件结构

```
workspace/skills/
├── daily-briefing/
│   └── SKILL.md
├── email-triage/
│   ├── SKILL.md
│   └── templates/
│       └── reply_template.md
├── meeting-prep/
│   ├── SKILL.md
│   └── references/
│       └── meeting_rules.md
└── code-review/
    ├── SKILL.md
    └── scripts/
        └── lint_check.sh
```

SKILL.md 示例：

```markdown
---
name: daily-briefing
description: 生成每日工作简报，包含日程、待办和昨日摘要
version: 1.0.0
---

# Daily Briefing

当被要求生成每日简报时：

1. 使用 recall_memory 检索昨日的工作记录
2. 使用 search_sessions 查找最近未完成的待办事项
3. 按以下格式组织输出：

## 格式
### 📅 今日日程
（列出今日安排，如无日历工具则标注"日历工具未接入，请手动确认"）

### ✅ 待办跟进
（从昨日对话中提取的未完成事项）

### 📋 昨日回顾
（按项目分组，每项不超过3行）

## 注意事项
- 遵循 MEMORY.md 中的日报格式偏好
- 如果找不到足够信息，如实说明而非编造
```

#### 3.5.2 按需加载机制

上下文组装时只注入 Skill Index（名称 + description 的紧凑列表）。Agent 判断 Skill 与当前任务相关后才调用 `read_skill` tool 读取完整内容。

```python
class SkillLoader:
    def get_index(self) -> str:
        """生成紧凑的 Skill 索引，注入系统提示词"""
        skills = self._scan_skills_dir()
        lines = ["Available Skills:"]
        for skill in skills:
            lines.append(f"  - {skill.name}: {skill.description}")
        lines.append(
            "\nUse read_skill(name) to load full instructions when needed."
        )
        return "\n".join(lines)

    def read_full(self, skill_name: str) -> str:
        """读取 Skill 完整内容 + 关联文件"""
        skill_dir = self.skills_dir / skill_name
        content = (skill_dir / "SKILL.md").read_text()
        refs_dir = skill_dir / "references"
        if refs_dir.exists():
            for ref_file in refs_dir.glob("*.md"):
                content += f"\n\n--- Reference: {ref_file.name} ---\n"
                content += ref_file.read_text()
        return content
```

### 3.6 模块六：Heartbeat 与 Cron 调度

**职责**：让 Agent 从被动响应变为主动助理。**[v1.1]** 新增 Cron 精确时间调度，与 Heartbeat 轮询互补。

#### 3.6.1 Heartbeat vs Cron 的区别

| 特性 | Heartbeat | Cron |
|------|-----------|------|
| 触发方式 | 固定间隔轮询 | 精确时间点 |
| 适用场景 | 检查是否有事做 | 在特定时间执行特定任务 |
| 任务定义 | HEARTBEAT.md（自然语言） | CRON.yaml（结构化配置） |
| 典型用例 | 检查 inbox、待办跟进 | 每天 8 点发简报、每周五生成周报 |

#### 3.6.2 Heartbeat 调度器

```python
class HeartbeatScheduler:
    def __init__(self, interval_minutes: int = 30):
        self.interval = interval_minutes * 60

    async def start(self, agent_core: AgentCore, gateway: Gateway):
        while True:
            await asyncio.sleep(self.interval)

            heartbeat_content = self._load_heartbeat_md()
            if not heartbeat_content.strip():
                continue

            internal_message = NormalizedMessage(
                message_id=f"heartbeat-{datetime.now().isoformat()}",
                session_id="__heartbeat__",
                sender="system",
                body=(
                    f"[HEARTBEAT] Current time: {datetime.now().isoformat()}\n"
                    f"Review the following task list and take action on anything "
                    f"that needs attention right now. If nothing needs doing, "
                    f"respond with exactly 'HEARTBEAT_OK'.\n\n"
                    f"{heartbeat_content}"
                ),
                channel="internal",
                metadata={"type": "heartbeat"},
                timestamp=datetime.now(),
            )

            result = await agent_core.run(internal_message)

            if result.strip() == "HEARTBEAT_OK":
                logger.debug("Heartbeat: nothing to do")
            else:
                # 推送到飞书（默认通道）
                await gateway.send_to_default_channel(result)
```

**HEARTBEAT.md** 示例：

```markdown
# Proactive Tasks

## Every Morning (8:00 UTC+8)
- 检查昨日会话中是否有未完成的待办事项，如有则推送提醒
- 使用 daily-briefing skill 生成今日简报

## Every 2 Hours
- 检查 workspace/inbox/ 目录是否有新文件需要处理

## Daily Evening (18:00 UTC+8)
- 生成今日工作摘要并推送到飞书
```

#### 3.6.3 Cron 定时任务 [v1.1 新增]

**Cron 配置格式**（`workspace/CRON.yaml`）：

```yaml
tasks:
  - name: "daily_briefing"
    description: "每日早间简报"
    schedule: "0 8 * * *"          # Cron 表达式: 每天 8:00
    timezone: "Asia/Shanghai"
    action:
      type: "skill"                # "skill" | "prompt" | "tool"
      skill: "daily-briefing"
    output:
      channel: "feishu"            # 输出到哪个通道
      session_id: "default"        # 或具体的 chat_id
    enabled: true

  - name: "weekly_report"
    description: "每周工作周报"
    schedule: "0 17 * * 5"         # 每周五 17:00
    timezone: "Asia/Shanghai"
    action:
      type: "prompt"
      prompt: |
        请生成本周的工作周报，包含：
        1. 本周完成的主要工作
        2. 遇到的问题和解决方案
        3. 下周计划
        使用 recall_memory 检索本周的对话记录。
    output:
      channel: "feishu"
    enabled: true

  - name: "inbox_check"
    description: "检查收件箱新文件"
    schedule: "*/30 * * * *"       # 每 30 分钟
    action:
      type: "tool"
      tool: "file_read"
      params:
        path: "workspace/inbox/"
    condition: "has_new_files"     # 条件判断（可选）
    enabled: false                 # 暂时禁用
```

**Cron 调度器实现**：

```python
from croniter import croniter
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import asyncio
import pytz

@dataclass
class CronTask:
    name: str
    description: str
    schedule: str                    # Cron 表达式
    timezone: str
    action: dict
    output: dict
    enabled: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None

class CronScheduler:
    """Cron 定时任务调度器"""

    def __init__(
        self,
        config_path: str = "workspace/CRON.yaml",
        agent_core: "AgentCore" = None,
        gateway: "Gateway" = None,
    ):
        self.config_path = config_path
        self.agent_core = agent_core
        self.gateway = gateway
        self._tasks: list[CronTask] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def load_tasks(self) -> None:
        """加载 Cron 配置"""
        import yaml
        from pathlib import Path

        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.warning(f"Cron config not found: {self.config_path}")
            return

        with open(config_file) as f:
            config = yaml.safe_load(f)

        self._tasks = []
        for task_config in config.get("tasks", []):
            task = CronTask(
                name=task_config["name"],
                description=task_config.get("description", ""),
                schedule=task_config["schedule"],
                timezone=task_config.get("timezone", "UTC"),
                action=task_config["action"],
                output=task_config.get("output", {}),
                enabled=task_config.get("enabled", True),
            )
            if task.enabled:
                task.next_run = self._calculate_next_run(task)
                self._tasks.append(task)

        logger.info(f"Loaded {len(self._tasks)} cron tasks")

    def _calculate_next_run(self, task: CronTask) -> datetime:
        """计算下次执行时间"""
        tz = pytz.timezone(task.timezone)
        now = datetime.now(tz)
        cron = croniter(task.schedule, now)
        return cron.get_next(datetime)

    async def start(self) -> None:
        """启动调度器"""
        self.load_tasks()
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Cron scheduler started")

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Cron scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """调度主循环"""
        while self._running:
            now = datetime.now(pytz.UTC)

            for task in self._tasks:
                if not task.enabled or not task.next_run:
                    continue

                # 转换时区比较
                task_tz = pytz.timezone(task.timezone)
                next_run_utc = task.next_run.astimezone(pytz.UTC)

                if now >= next_run_utc:
                    # 执行任务
                    asyncio.create_task(self._execute_task(task))
                    # 计算下次执行时间
                    task.last_run = now
                    task.next_run = self._calculate_next_run(task)

            # 每 10 秒检查一次
            await asyncio.sleep(10)

    async def _execute_task(self, task: CronTask) -> None:
        """执行单个任务"""
        logger.info(f"Executing cron task: {task.name}")

        try:
            action = task.action
            result = None

            if action["type"] == "skill":
                prompt = f"请使用 {action['skill']} skill 执行任务。"
                result = await self._run_agent(prompt, task)

            elif action["type"] == "prompt":
                result = await self._run_agent(action["prompt"], task)

            elif action["type"] == "tool":
                prompt = f"请执行 {action['tool']} 工具，参数：{action.get('params', {})}"
                result = await self._run_agent(prompt, task)

            # 输出结果
            if result and task.output:
                await self._send_output(result, task.output)

            logger.info(f"Cron task completed: {task.name}")

        except Exception as e:
            logger.error(f"Cron task failed: {task.name}, error: {e}")

    async def _run_agent(self, prompt: str, task: CronTask) -> str:
        """通过 Agent 执行任务"""
        message = NormalizedMessage(
            message_id=f"cron-{task.name}-{datetime.now().isoformat()}",
            session_id=f"__cron_{task.name}__",
            sender="cron",
            body=f"[CRON TASK: {task.name}]\n{prompt}",
            attachments=[],
            channel="internal",
            metadata={"type": "cron", "task_name": task.name},
            timestamp=datetime.now(),
        )
        return await self.agent_core.run(message)

    async def _send_output(self, result: str, output_config: dict) -> None:
        """发送任务输出"""
        channel = output_config.get("channel", "feishu")
        session_id = output_config.get("session_id", "default")

        if channel == "feishu" and self.gateway:
            await self.gateway.send_to_channel("feishu", session_id, result)
```

### 3.7 模块七：LLM Provider 抽象层 [v1.1 新增]

**职责**：提供统一的 LLM 调用接口，支持多 Provider 注册、自动 Fallback、健康检查。

#### 3.7.1 设计目标

- 统一的 Provider 抽象接口，支持任意数量的 Provider 注册
- 所有 OpenAI API 兼容服务通过同一个适配器接入
- 灵活的路由策略：按用途（primary/fallback/compaction）、按成本、按延迟
- 运行时动态切换 Provider（不重启）

#### 3.7.2 Provider 抽象接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from enum import Enum

class ProviderCapability(Enum):
    """Provider 能力标记"""
    CHAT = "chat"
    TOOL_USE = "tool_use"
    VISION = "vision"
    STREAMING = "streaming"
    LONG_CONTEXT = "long_context"  # >100k tokens

@dataclass
class ProviderConfig:
    """Provider 配置"""
    name: str                          # 唯一标识: "anthropic-sonnet", "deepseek-chat"
    type: str                          # 类型: "anthropic" | "openai" | "openai_compatible"
    model: str                         # 模型名: "claude-sonnet-4", "deepseek-chat"
    api_key_env: str                   # API Key 环境变量名
    base_url: Optional[str] = None     # OpenAI 兼容服务的 base_url
    max_context: int = 128000          # 上下文窗口大小
    max_output: int = 8192             # 最大输出 token
    cost_per_1m_input: float = 0.0     # 输入成本 ($/1M tokens)
    cost_per_1m_output: float = 0.0    # 输出成本 ($/1M tokens)
    capabilities: list[ProviderCapability] = None
    timeout: int = 120                 # 请求超时秒数
    retry_count: int = 3               # 重试次数
    retry_delay: float = 1.0           # 重试间隔

@dataclass
class LLMRequest:
    """统一的 LLM 请求格式"""
    messages: list[dict]               # OpenAI 格式的消息列表
    tools: Optional[list[dict]] = None # 工具定义 (JSON Schema)
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    stop: Optional[list[str]] = None

@dataclass
class LLMResponse:
    """统一的 LLM 响应格式"""
    type: str                          # "text" | "tool_calls"
    text: Optional[str] = None         # 文本回复
    tool_calls: Optional[list] = None  # 工具调用列表
    usage: Optional[dict] = None       # token 使用统计
    model: str = ""                    # 实际使用的模型
    provider: str = ""                 # 实际使用的 provider
    latency_ms: int = 0                # 响应延迟

class LLMProvider(ABC):
    """LLM Provider 抽象基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client = None

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def initialize(self) -> None:
        """初始化 Provider（建立连接、验证 API Key）"""
        pass

    @abstractmethod
    async def call(self, request: LLMRequest) -> LLMResponse:
        """同步调用 LLM"""
        pass

    @abstractmethod
    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """流式调用 LLM"""
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """计算 token 数量"""
        pass

    def supports(self, capability: ProviderCapability) -> bool:
        """检查是否支持某项能力"""
        return capability in (self.config.capabilities or [])
```

#### 3.7.3 Provider 实现

**Anthropic Provider**：

```python
import anthropic

class AnthropicProvider(LLMProvider):
    """Anthropic Claude Provider"""

    async def initialize(self) -> None:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise ValueError(f"Missing API key: {self.config.api_key_env}")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def call(self, request: LLMRequest) -> LLMResponse:
        start_time = time.time()

        # 转换消息格式: OpenAI → Anthropic
        system_prompt, messages = self._convert_messages(request.messages)

        response = await self._client.messages.create(
            model=self.config.model,
            system=system_prompt,
            messages=messages,
            tools=self._convert_tools(request.tools) if request.tools else None,
            max_tokens=request.max_tokens or self.config.max_output,
            temperature=request.temperature,
        )

        latency_ms = int((time.time() - start_time) * 1000)
        return self._convert_response(response, latency_ms)

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        system_prompt, messages = self._convert_messages(request.messages)

        async with self._client.messages.stream(
            model=self.config.model,
            system=system_prompt,
            messages=messages,
            max_tokens=request.max_tokens or self.config.max_output,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    def count_tokens(self, text: str) -> int:
        return self._client.count_tokens(text)

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list]:
        """OpenAI 消息格式 → Anthropic 格式"""
        system_prompt = ""
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
            else:
                converted.append({"role": msg["role"], "content": msg["content"]})
        return system_prompt.strip(), converted
```

**OpenAI Compatible Provider**：

```python
from openai import AsyncOpenAI

class OpenAICompatibleProvider(LLMProvider):
    """OpenAI API 兼容 Provider（支持 OpenAI, DeepSeek, Moonshot, Groq, Ollama 等）"""

    async def initialize(self) -> None:
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key and self.config.base_url not in ["http://localhost"]:
            raise ValueError(f"Missing API key: {self.config.api_key_env}")

        self._client = AsyncOpenAI(
            api_key=api_key or "not-needed",
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

    async def call(self, request: LLMRequest) -> LLMResponse:
        start_time = time.time()

        kwargs = {
            "model": self.config.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens or self.config.max_output,
        }

        if request.tools:
            kwargs["tools"] = request.tools
            kwargs["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**kwargs)
        latency_ms = int((time.time() - start_time) * 1000)

        return self._convert_response(response, latency_ms)

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        kwargs = {
            "model": self.config.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": True,
        }

        async for chunk in await self._client.chat.completions.create(**kwargs):
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def count_tokens(self, text: str) -> int:
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(self.config.model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
```

#### 3.7.4 Provider Manager（路由管理器）

```python
class ProviderRole(Enum):
    """Provider 角色"""
    PRIMARY = "primary"           # 主力模型
    FALLBACK = "fallback"         # 备用模型
    COMPACTION = "compaction"     # 压缩/摘要专用
    EMBEDDING = "embedding"       # 向量化专用

class ProviderManager:
    """Provider 管理器 - 负责注册、路由、Fallback"""

    def __init__(self):
        self._providers: dict[str, LLMProvider] = {}
        self._role_mapping: dict[ProviderRole, list[str]] = {
            role: [] for role in ProviderRole
        }
        self._health_status: dict[str, bool] = {}

    async def register(
        self,
        config: ProviderConfig,
        roles: list[ProviderRole] = None
    ) -> None:
        """注册 Provider"""
        provider_class = self._get_provider_class(config.type)
        provider = provider_class(config)

        try:
            await provider.initialize()
            self._health_status[config.name] = True
        except Exception as e:
            logger.warning(f"Provider {config.name} initialization failed: {e}")
            self._health_status[config.name] = False
            return

        self._providers[config.name] = provider

        for role in (roles or [ProviderRole.PRIMARY]):
            self._role_mapping[role].append(config.name)

        logger.info(f"Registered provider: {config.name} with roles {roles}")

    def _get_provider_class(self, provider_type: str) -> type:
        """根据类型获取 Provider 实现类"""
        mapping = {
            "anthropic": AnthropicProvider,
            "openai": OpenAICompatibleProvider,
            "openai_compatible": OpenAICompatibleProvider,
        }
        if provider_type not in mapping:
            raise ValueError(f"Unknown provider type: {provider_type}")
        return mapping[provider_type]

    async def call(
        self,
        request: LLMRequest,
        role: ProviderRole = ProviderRole.PRIMARY,
        preferred_provider: str = None
    ) -> LLMResponse:
        """调用 LLM（带自动 Fallback）"""
        providers_to_try = self._get_providers_for_role(role, preferred_provider)

        last_error = None
        for provider_name in providers_to_try:
            provider = self._providers.get(provider_name)
            if not provider or not self._health_status.get(provider_name, False):
                continue

            try:
                response = await provider.call(request)
                response.provider = provider_name
                return response

            except Exception as e:
                logger.warning(f"Provider {provider_name} failed: {e}")
                last_error = e
                self._health_status[provider_name] = False
                continue

        raise RuntimeError(
            f"All providers failed for role {role}. Last error: {last_error}"
        )

    def _get_providers_for_role(
        self,
        role: ProviderRole,
        preferred: str = None
    ) -> list[str]:
        """获取角色对应的 Provider 列表（按优先级排序）"""
        providers = self._role_mapping.get(role, []).copy()

        if preferred and preferred in providers:
            providers.remove(preferred)
            providers.insert(0, preferred)

        if not providers and role == ProviderRole.PRIMARY:
            providers = self._role_mapping.get(ProviderRole.FALLBACK, [])

        return providers

    async def health_check(self) -> dict[str, bool]:
        """健康检查所有 Provider"""
        for name, provider in self._providers.items():
            try:
                await provider.call(LLMRequest(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ))
                self._health_status[name] = True
            except Exception:
                self._health_status[name] = False
        return self._health_status.copy()

    def list_providers(self) -> list[dict]:
        """列出所有 Provider 状态"""
        return [
            {
                "name": name,
                "model": provider.config.model,
                "healthy": self._health_status.get(name, False),
                "capabilities": [c.value for c in (provider.config.capabilities or [])],
            }
            for name, provider in self._providers.items()
        ]
```

#### 3.7.5 Provider 配置文件（`config/providers.yaml`）

```yaml
providers:
  # Anthropic Claude 系列
  - name: "claude-sonnet"
    type: "anthropic"
    model: "claude-sonnet-4-20250514"
    api_key_env: "ANTHROPIC_API_KEY"
    max_context: 200000
    max_output: 8192
    cost_per_1m_input: 3.0
    cost_per_1m_output: 15.0
    capabilities: ["chat", "tool_use", "vision", "streaming", "long_context"]
    roles: ["primary"]

  - name: "claude-haiku"
    type: "anthropic"
    model: "claude-haiku-3"
    api_key_env: "ANTHROPIC_API_KEY"
    max_context: 200000
    max_output: 4096
    cost_per_1m_input: 0.25
    cost_per_1m_output: 1.25
    capabilities: ["chat", "streaming"]
    roles: ["compaction"]

  # OpenAI
  - name: "gpt-4o"
    type: "openai"
    model: "gpt-4o"
    api_key_env: "OPENAI_API_KEY"
    max_context: 128000
    max_output: 4096
    cost_per_1m_input: 2.5
    cost_per_1m_output: 10.0
    capabilities: ["chat", "tool_use", "vision", "streaming"]
    roles: ["fallback"]

  # DeepSeek（OpenAI 兼容）
  - name: "deepseek-chat"
    type: "openai_compatible"
    model: "deepseek-chat"
    base_url: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY"
    max_context: 64000
    max_output: 4096
    cost_per_1m_input: 0.14
    cost_per_1m_output: 0.28
    capabilities: ["chat", "tool_use", "streaming"]
    roles: ["fallback"]

  # 本地 Ollama（无需 API Key）
  - name: "ollama-qwen"
    type: "openai_compatible"
    model: "qwen2.5:32b"
    base_url: "http://localhost:11434/v1"
    api_key_env: "OLLAMA_API_KEY"  # 可以设置为任意值
    max_context: 32000
    max_output: 4096
    capabilities: ["chat", "streaming"]
    roles: []  # 手动指定时使用

  # Azure OpenAI
  - name: "azure-gpt4"
    type: "openai_compatible"
    model: "gpt-4"
    base_url: "https://your-resource.openai.azure.com/openai/deployments/gpt-4"
    api_key_env: "AZURE_OPENAI_API_KEY"
    max_context: 128000
    capabilities: ["chat", "tool_use", "streaming"]
    roles: ["fallback"]
```

### 3.8 模块八：Session 管理 [v1.1 新增]

**职责**：管理 Session 的完整生命周期，包括创建、活跃、过期、归档，支持进程重启后恢复和群聊多用户隔离。

#### 3.8.1 Session 数据模型

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

class SessionState(Enum):
    ACTIVE = "active"           # 活跃中
    IDLE = "idle"               # 空闲（可恢复）
    EXPIRED = "expired"         # 已过期（待归档）
    ARCHIVED = "archived"       # 已归档

@dataclass
class SessionMetadata:
    """Session 元数据"""
    session_id: str
    channel: str                         # "cli" | "feishu"
    chat_type: str                       # "p2p" | "group"
    created_at: datetime
    last_active_at: datetime
    state: SessionState = SessionState.ACTIVE
    message_count: int = 0
    token_count: int = 0
    participants: list[str] = field(default_factory=list)  # 群聊参与者
    context_summary: Optional[str] = None  # 上下文摘要（用于恢复）

@dataclass
class Session:
    """完整 Session"""
    metadata: SessionMetadata
    history: list[dict] = field(default_factory=list)  # 对话历史

    def append(self, message: dict) -> None:
        self.history.append(message)
        self.metadata.message_count += 1
        self.metadata.last_active_at = datetime.now()

    def get_history(self, max_turns: int = None) -> list[dict]:
        if max_turns:
            return self.history[-max_turns * 2:]  # 每轮 2 条消息
        return self.history

    def last_turn(self) -> list[dict]:
        return self.history[-2:] if len(self.history) >= 2 else self.history
```

#### 3.8.2 Session Manager

```python
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import asyncio

class SessionManager:
    """Session 管理器"""

    def __init__(
        self,
        db_path: str = "workspace/sessions.db",
        idle_timeout: timedelta = timedelta(hours=2),
        expire_timeout: timedelta = timedelta(days=7),
    ):
        self.db_path = db_path
        self.idle_timeout = idle_timeout
        self.expire_timeout = expire_timeout
        self._active_sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_metadata (
                session_id TEXT PRIMARY KEY,
                channel TEXT,
                chat_type TEXT,
                created_at TEXT,
                last_active_at TEXT,
                state TEXT,
                message_count INTEGER,
                token_count INTEGER,
                participants TEXT,       -- JSON array
                context_summary TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_history (
                session_id TEXT,
                turn_index INTEGER,
                role TEXT,
                content TEXT,
                tool_calls TEXT,         -- JSON, nullable
                created_at TEXT,
                PRIMARY KEY (session_id, turn_index)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_state
            ON session_metadata(state)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_last_active
            ON session_metadata(last_active_at)
        """)
        conn.commit()
        conn.close()

    async def get_or_create(
        self,
        session_id: str,
        channel: str = "cli",
        chat_type: str = "p2p",
        sender: str = None
    ) -> Session:
        """获取或创建 Session"""
        async with self._lock:
            # 1. 检查内存中的活跃 Session
            if session_id in self._active_sessions:
                session = self._active_sessions[session_id]
                session.metadata.last_active_at = datetime.now()
                session.metadata.state = SessionState.ACTIVE
                if sender and sender not in session.metadata.participants:
                    session.metadata.participants.append(sender)
                return session

            # 2. 尝试从数据库恢复
            session = await self._restore_from_db(session_id)
            if session:
                session.metadata.state = SessionState.ACTIVE
                session.metadata.last_active_at = datetime.now()
                self._active_sessions[session_id] = session
                return session

            # 3. 创建新 Session
            metadata = SessionMetadata(
                session_id=session_id,
                channel=channel,
                chat_type=chat_type,
                created_at=datetime.now(),
                last_active_at=datetime.now(),
                state=SessionState.ACTIVE,
                participants=[sender] if sender else [],
            )
            session = Session(metadata=metadata)
            self._active_sessions[session_id] = session
            return session

    async def _restore_from_db(self, session_id: str) -> Optional[Session]:
        """从数据库恢复 Session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 获取元数据
        cursor.execute(
            "SELECT * FROM session_metadata WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None

        metadata = SessionMetadata(
            session_id=row[0],
            channel=row[1],
            chat_type=row[2],
            created_at=datetime.fromisoformat(row[3]),
            last_active_at=datetime.fromisoformat(row[4]),
            state=SessionState(row[5]),
            message_count=row[6],
            token_count=row[7],
            participants=json.loads(row[8]) if row[8] else [],
            context_summary=row[9],
        )

        # 获取历史记录（最近 N 轮）
        cursor.execute("""
            SELECT role, content, tool_calls FROM session_history
            WHERE session_id = ?
            ORDER BY turn_index DESC
            LIMIT 20
        """, (session_id,))
        history = []
        for row in reversed(cursor.fetchall()):
            msg = {"role": row[0], "content": row[1]}
            if row[2]:
                msg["tool_calls"] = json.loads(row[2])
            history.append(msg)

        conn.close()
        return Session(metadata=metadata, history=history)

    async def persist(self, session: Session) -> None:
        """持久化 Session 到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 更新元数据
        cursor.execute("""
            INSERT OR REPLACE INTO session_metadata
            (session_id, channel, chat_type, created_at, last_active_at,
             state, message_count, token_count, participants, context_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.metadata.session_id,
            session.metadata.channel,
            session.metadata.chat_type,
            session.metadata.created_at.isoformat(),
            session.metadata.last_active_at.isoformat(),
            session.metadata.state.value,
            session.metadata.message_count,
            session.metadata.token_count,
            json.dumps(session.metadata.participants),
            session.metadata.context_summary,
        ))

        # 增量写入历史（只写最后几条）
        base_index = max(0, session.metadata.message_count - len(session.history))
        for i, msg in enumerate(session.history[-10:]):
            cursor.execute("""
                INSERT OR REPLACE INTO session_history
                (session_id, turn_index, role, content, tool_calls, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session.metadata.session_id,
                base_index + i,
                msg["role"],
                msg.get("content", ""),
                json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                datetime.now().isoformat(),
            ))

        conn.commit()
        conn.close()

    async def cleanup_idle_sessions(self) -> int:
        """清理空闲 Session（从内存移除，保留数据库记录）"""
        async with self._lock:
            now = datetime.now()
            to_remove = []

            for session_id, session in self._active_sessions.items():
                idle_duration = now - session.metadata.last_active_at
                if idle_duration > self.idle_timeout:
                    session.metadata.state = SessionState.IDLE
                    await self.persist(session)
                    to_remove.append(session_id)

            for session_id in to_remove:
                del self._active_sessions[session_id]

            return len(to_remove)

    async def archive_expired_sessions(self) -> int:
        """归档过期 Session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        expire_threshold = (datetime.now() - self.expire_timeout).isoformat()
        cursor.execute("""
            UPDATE session_metadata
            SET state = ?
            WHERE state IN (?, ?) AND last_active_at < ?
        """, (
            SessionState.EXPIRED.value,
            SessionState.ACTIVE.value,
            SessionState.IDLE.value,
            expire_threshold,
        ))

        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected

    def get_active_count(self) -> int:
        """获取活跃 Session 数量"""
        return len(self._active_sessions)
```

#### 3.8.3 群聊多用户隔离

```python
class GroupChatSessionStrategy:
    """群聊 Session 策略"""

    def __init__(self, isolation_mode: str = "shared"):
        """
        isolation_mode:
        - "shared": 同一群聊共享一个 Session（默认）
        - "per_user": 每个用户独立 Session（群聊中每人对话独立）
        - "per_topic": 按话题隔离（需要 LLM 判断话题边界）
        """
        self.isolation_mode = isolation_mode

    def get_session_id(
        self,
        chat_id: str,
        sender_id: str,
        message_content: str = None
    ) -> str:
        """根据策略生成 Session ID"""
        if self.isolation_mode == "shared":
            return f"group:{chat_id}"

        elif self.isolation_mode == "per_user":
            return f"group:{chat_id}:user:{sender_id}"

        elif self.isolation_mode == "per_topic":
            # 简化实现：使用 hash 做话题聚类
            return f"group:{chat_id}"

        return f"group:{chat_id}"
```

#### 3.8.4 Session 清理调度

```python
class SessionCleanupScheduler:
    """Session 清理调度器"""

    def __init__(
        self,
        session_manager: SessionManager,
        cleanup_interval: int = 300,  # 5 分钟
    ):
        self.session_manager = session_manager
        self.cleanup_interval = cleanup_interval
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cleanup_interval)
            try:
                idle_count = await self.session_manager.cleanup_idle_sessions()
                expired_count = await self.session_manager.archive_expired_sessions()
                if idle_count or expired_count:
                    logger.info(
                        "Session cleanup completed",
                        idle_removed=idle_count,
                        expired_archived=expired_count
                    )
            except Exception as e:
                logger.error(f"Session cleanup failed: {e}")
```

### 3.9 模块九：观测性系统 [v1.1 新增]

**职责**：提供系统运行时的可观测能力，包括指标收集、链路追踪、结构化日志。

#### 3.9.1 Metrics 收集

```python
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import threading

@dataclass
class MetricsSnapshot:
    """指标快照"""
    timestamp: datetime
    # LLM 调用
    llm_calls_total: int = 0
    llm_calls_success: int = 0
    llm_calls_failed: int = 0
    llm_latency_ms_avg: float = 0.0
    llm_latency_ms_p99: float = 0.0
    # Token 使用
    tokens_input_total: int = 0
    tokens_output_total: int = 0
    tokens_cost_usd: float = 0.0
    # 工具调用
    tool_calls_total: int = 0
    tool_calls_by_name: dict = field(default_factory=dict)
    tool_calls_failed: int = 0
    # Session
    active_sessions: int = 0
    messages_processed: int = 0
    # Memory
    mflow_ingestions: int = 0
    mflow_queries: int = 0
    session_archive_writes: int = 0

class MetricsCollector:
    """指标收集器 - 线程安全"""

    def __init__(self):
        self._lock = threading.Lock()
        self._current = MetricsSnapshot(timestamp=datetime.now())
        self._latencies: list[float] = []
        self._cost_by_provider: dict[str, float] = defaultdict(float)

    def record_llm_call(
        self,
        provider: str,
        success: bool,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float
    ) -> None:
        with self._lock:
            self._current.llm_calls_total += 1
            if success:
                self._current.llm_calls_success += 1
            else:
                self._current.llm_calls_failed += 1

            self._latencies.append(latency_ms)
            self._current.tokens_input_total += input_tokens
            self._current.tokens_output_total += output_tokens
            self._current.tokens_cost_usd += cost_usd
            self._cost_by_provider[provider] += cost_usd

    def record_tool_call(self, tool_name: str, success: bool) -> None:
        with self._lock:
            self._current.tool_calls_total += 1
            self._current.tool_calls_by_name[tool_name] = \
                self._current.tool_calls_by_name.get(tool_name, 0) + 1
            if not success:
                self._current.tool_calls_failed += 1

    def record_message_processed(self) -> None:
        with self._lock:
            self._current.messages_processed += 1

    def record_mflow_operation(self, operation: str) -> None:
        with self._lock:
            if operation == "ingest":
                self._current.mflow_ingestions += 1
            elif operation == "query":
                self._current.mflow_queries += 1

    def get_snapshot(self) -> MetricsSnapshot:
        """获取当前指标快照"""
        with self._lock:
            snapshot = MetricsSnapshot(
                timestamp=datetime.now(),
                llm_calls_total=self._current.llm_calls_total,
                llm_calls_success=self._current.llm_calls_success,
                llm_calls_failed=self._current.llm_calls_failed,
                tokens_input_total=self._current.tokens_input_total,
                tokens_output_total=self._current.tokens_output_total,
                tokens_cost_usd=self._current.tokens_cost_usd,
                tool_calls_total=self._current.tool_calls_total,
                tool_calls_by_name=self._current.tool_calls_by_name.copy(),
                tool_calls_failed=self._current.tool_calls_failed,
                messages_processed=self._current.messages_processed,
                mflow_ingestions=self._current.mflow_ingestions,
                mflow_queries=self._current.mflow_queries,
            )

            # 计算延迟统计
            if self._latencies:
                snapshot.llm_latency_ms_avg = sum(self._latencies) / len(self._latencies)
                sorted_latencies = sorted(self._latencies)
                p99_idx = int(len(sorted_latencies) * 0.99)
                snapshot.llm_latency_ms_p99 = sorted_latencies[p99_idx]

            return snapshot

    def reset(self) -> None:
        """重置指标（通常每天或每小时）"""
        with self._lock:
            self._current = MetricsSnapshot(timestamp=datetime.now())
            self._latencies.clear()
            self._cost_by_provider.clear()

# 全局实例
metrics = MetricsCollector()
```

#### 3.9.2 Tracing（链路追踪）

```python
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# 当前 trace 上下文
_current_trace: ContextVar[Optional["Trace"]] = ContextVar("current_trace", default=None)

@dataclass
class Span:
    """追踪单元"""
    span_id: str
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    status: str = "ok"  # "ok" | "error"
    error_message: Optional[str] = None

    def add_event(self, name: str, attributes: dict = None) -> None:
        self.events.append({
            "name": name,
            "timestamp": datetime.now().isoformat(),
            "attributes": attributes or {}
        })

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value

    def finish(self, status: str = "ok", error: str = None) -> None:
        self.end_time = datetime.now()
        self.status = status
        self.error_message = error

@dataclass
class Trace:
    """完整追踪链路"""
    trace_id: str
    session_id: str
    message_id: str
    start_time: datetime
    spans: list[Span] = field(default_factory=list)
    _current_span: Optional[Span] = field(default=None, repr=False)

    def start_span(self, name: str, attributes: dict = None) -> Span:
        span = Span(
            span_id=str(uuid.uuid4())[:8],
            name=name,
            start_time=datetime.now(),
            attributes=attributes or {}
        )
        self.spans.append(span)
        self._current_span = span
        return span

    def current_span(self) -> Optional[Span]:
        return self._current_span

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "start_time": self.start_time.isoformat(),
            "duration_ms": self._calculate_duration(),
            "spans": [
                {
                    "span_id": s.span_id,
                    "name": s.name,
                    "duration_ms": (s.end_time - s.start_time).total_seconds() * 1000
                        if s.end_time else None,
                    "status": s.status,
                    "attributes": s.attributes,
                    "events": s.events,
                }
                for s in self.spans
            ]
        }

    def _calculate_duration(self) -> float:
        if not self.spans:
            return 0
        last_end = max(
            (s.end_time for s in self.spans if s.end_time),
            default=self.start_time
        )
        return (last_end - self.start_time).total_seconds() * 1000

class Tracer:
    """追踪管理器"""

    def __init__(self, storage_path: str = "workspace/traces"):
        self.storage_path = storage_path
        self._traces: list[Trace] = []

    def start_trace(self, session_id: str, message_id: str) -> Trace:
        trace = Trace(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            message_id=message_id,
            start_time=datetime.now(),
        )
        _current_trace.set(trace)
        self._traces.append(trace)
        return trace

    def current_trace(self) -> Optional[Trace]:
        return _current_trace.get()

    def end_trace(self) -> None:
        trace = _current_trace.get()
        if trace:
            self._persist_trace(trace)
            _current_trace.set(None)

    def _persist_trace(self, trace: Trace) -> None:
        """持久化 trace 到文件（按日期分文件）"""
        import json
        from pathlib import Path

        date_str = trace.start_time.strftime("%Y-%m-%d")
        file_path = Path(self.storage_path) / f"{date_str}.jsonl"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "a") as f:
            f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")

# 全局实例
tracer = Tracer()
```

#### 3.9.3 结构化日志

```python
import logging
import json
from datetime import datetime
from typing import Any

class StructuredFormatter(logging.Formatter):
    """结构化 JSON 日志格式"""

    SENSITIVE_KEYS = {"api_key", "password", "secret", "token", "authorization"}

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 添加额外字段
        if hasattr(record, "extra_fields"):
            log_data.update(self._sanitize(record.extra_fields))

        # 添加 trace 信息
        trace = tracer.current_trace()
        if trace:
            log_data["trace_id"] = trace.trace_id
            log_data["session_id"] = trace.session_id

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)

    def _sanitize(self, data: dict) -> dict:
        """脱敏处理"""
        sanitized = {}
        for key, value in data.items():
            if any(s in key.lower() for s in self.SENSITIVE_KEYS):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize(value)
            else:
                sanitized[key] = value
        return sanitized

class AgentLogger:
    """Agent 专用日志器"""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def info(self, message: str, **kwargs) -> None:
        self._log(logging.INFO, message, kwargs)

    def warning(self, message: str, **kwargs) -> None:
        self._log(logging.WARNING, message, kwargs)

    def error(self, message: str, **kwargs) -> None:
        self._log(logging.ERROR, message, kwargs)

    def debug(self, message: str, **kwargs) -> None:
        self._log(logging.DEBUG, message, kwargs)

    def _log(self, level: int, message: str, extra: dict) -> None:
        record = self._logger.makeRecord(
            self._logger.name, level, "", 0, message, (), None
        )
        record.extra_fields = extra
        self._logger.handle(record)

# 使用示例
logger = AgentLogger("agent.core")
# logger.info("LLM call completed", provider="claude-sonnet", latency_ms=1234, tokens=500)
```

---

## 4. 数据流全景

### 4.1 一条消息的完整生命周期

```
用户在飞书私聊发送: "帮我回顾一下上周关于技术选型的讨论"
    │
    ▼
[1] 飞书 Adapter 通过 lark-oapi WebSocket 长连接接收事件
    → _on_message_receive 回调触发
    → 解析 im.message.receive_v1 事件体
    → 标准化为 NormalizedMessage {session_id=chat_id, channel:"feishu", ...}
    → 放入 _message_queue
    │
    ▼
[2] Gateway Router 从队列取出消息
    → 识别 session_id
    → 放入该 session 的 Command Queue
    │
    ▼
[3] Command Queue 串行处理到该消息
    → 交给 Agent Core
    │
    ▼
[4] Context Assembly 组装上下文:
    [System Prompt] + [SOUL.md] + [MEMORY.md]
    + [Skill Index: "daily-briefing: ..., email-triage: ..."]
    + [Tool Schemas: file_read, shell_exec, web_search,
       memory_write, search_sessions, recall_memory, read_skill]
    + [Session History]
    + [User Message: "帮我回顾一下上周关于技术选型的讨论"]
    │
    ▼
[5] ReAct Loop - Iteration 1:
    LLM 思考 → "这是一个跨会话的回忆性问题，需要用 recall_memory 深度检索"
    LLM 输出 → tool_call: recall_memory("上周技术选型讨论")
    执行 → M-flow 图路由 Bundle Search
         → 命中 FacetPoint "用户倾向不引入外部数据库依赖"
         → 图传播至 Facet "SQLite vs Redis 对比分析"
         → 上溯至 Episode "技术选型会议讨论 (2026-04-15)"
         → 返回完整 Episode bundle
    上下文追加 tool_result
    │
    ▼
[6] ReAct Loop - Iteration 2:
    LLM 思考 → "M-flow 返回了核心 Episode，让我补充检索更多细节"
    LLM 输出 → tool_call: search_sessions("技术选型 数据库", limit=3)
    执行 → SQLite FTS5 匹配 → 返回 3 条相关会话片段（带时间戳）
    上下文追加 tool_result
    │
    ▼
[7] ReAct Loop - Iteration 3:
    LLM 思考 → "信息充足，可以生成回顾摘要了"
    LLM 输出 → text_response: 结构化的技术选型回顾
    │
    ▼
[8] Post-Turn 处理:
    ├── 同步: Session Archive → SQLite 写入本轮完整对话
    ├── 同步: Session Manager → 持久化 Session 元数据 [v1.1]
    ├── 异步: M-flow Bridge → m_flow.add() + m_flow.memorize()
    ├── 同步: Metrics → 记录 LLM 调用指标 [v1.1]
    ├── 同步: Tracer → 结束链路追踪 [v1.1]
    └── 同步: 飞书 Adapter → lark_client.im.v1.message.create()
              → 回复到飞书私聊
```

### 4.2 M-flow 数据写入与检索流

```
写入流 (异步，不阻塞响应):
━━━━━━━━━━━━━━━━━━━━━━━━━

对话轮次完成
    │
    ▼
mflow_bridge.ingest_turn()
    │
    ├── m_flow.add(formatted_conversation)
    │   └── 原始文本进入待处理队列
    │
    └── m_flow.memorize()
        ├── 文本分块 + 共指消解
        │   (代词 → 具体实体名, 在入库前完成)
        │   示例: "她说她不知道" → "Maria说Maria不知道"
        ├── 抽取 Episode / Facet / FacetPoint / Entity
        ├── 构建语义边 (边带自然语言描述，可被向量搜索命中)
        ├── 所有节点和边文本向量化
        └── 写入 LanceDB (本地嵌入式文件)


检索流 (Agent 主动调用):
━━━━━━━━━━━━━━━━━━━━━━━━

Agent 调用 recall_memory("为什么上周决定不用Redis？")
    │
    ▼
m_flow.query(question, mode="EPISODIC")
    │
    ├── Phase 1: Cast Wide Net (向量搜索找入口锚点)
    │   对 7 个向量集合并行搜索
    │   (Episode / Facet / FacetPoint / Entity + 对应 edge_text)
    │   每个集合返回最多 100 个候选
    │
    ├── Phase 2: Project into Graph (投射到知识图谱)
    │   候选锚点映射到图中，展开一跳邻居
    │   形成连通子图
    │
    ├── Phase 3: Path-Cost Propagation (路径代价传播)
    │   从锥尖 → 锥底传播:
    │     路径代价 = 起点向量距离 + Σ(边向量距离 + hop惩罚)
    │   Episode 最终得分 = min(所有路径代价)
    │   设计: 一条强证据链即可 (min, not avg)
    │   设计: 直接命中 Episode summary 被额外惩罚 (防宽泛匹配)
    │
    ├── Phase 4: Rank & Assemble
    │   按 bundle cost 排序，取 top_k
    │   返回 Episode bundle (含 Facet / FacetPoint / Entity)
    │
    └── 返回给 Agent:
        "Episode: 技术选型讨论 (2026-04-15)
         - Facet: Redis vs SQLite 对比
         - FacetPoint: '用户明确表示不想引入外部数据库依赖'
         - FacetPoint: 'SQLite FTS5 已满足当前检索需求'
         - Entity: Redis → 关联其他提及 Redis 的上下文"
```

---

## 5. 配置文件设计

### 5.1 主配置 agent.yaml

```yaml
agent:
  name: "Atlas"
  workspace_dir: "./workspace"
  max_iterations: 25
  compaction_reserve: 4096

# [v1.1] Provider 配置改为引用独立文件
providers:
  config_file: "config/providers.yaml"
  default_primary: "claude-sonnet"    # 默认主力 Provider
  default_fallback: "gpt-4o"          # 默认备用 Provider
  default_compaction: "claude-haiku"  # 默认压缩 Provider
  health_check_interval: 300          # 健康检查间隔（秒）

gateway:
  channels:
    - type: "cli"
      enabled: true
    - type: "feishu"
      enabled: true
      app_id_env: "FEISHU_APP_ID"
      app_secret_env: "FEISHU_APP_SECRET"
      default_channel: true         # Heartbeat 输出发送到此通道
      group_chat_mode: "mention"    # 群聊中仅响应 @机器人 消息
      group_session_strategy: "shared"  # [v1.1] "shared" | "per_user" | "per_topic"

memory:
  always_on:
    soul_file: "workspace/SOUL.md"
    memory_file: "workspace/MEMORY.md"
    max_memory_chars: 3500
  session_archive:
    db_path: "workspace/sessions.db"
  mflow:
    enabled: true
    data_dir: "./mflow_data"
    db_type: "lancedb"
    embedding_model: "text-embedding-3-small"
    default_recall_mode: "EPISODIC"
    default_top_k: 3
    async_ingestion: true

# [v1.1] Session 管理配置
session:
  idle_timeout_hours: 2              # 空闲超时（从内存移除）
  expire_timeout_days: 7             # 过期超时（归档）
  cleanup_interval_minutes: 5        # 清理检查间隔
  max_history_per_session: 100       # 每个 Session 最大保留消息数
  restore_on_startup: true           # 启动时恢复活跃 Session

skills:
  dir: "workspace/skills"

heartbeat:
  enabled: true
  interval_minutes: 30
  heartbeat_file: "workspace/HEARTBEAT.md"

# [v1.1] Cron 定时任务
cron:
  enabled: true
  config_file: "workspace/CRON.yaml"

safety:
  approval_required_tools:
    - "shell_exec"
    - "file_delete"
  approval_timeout_seconds: 300

# [v1.1] 观测性配置
observability:
  metrics:
    enabled: true
    reset_interval: "1h"
    export_path: "workspace/metrics"
  tracing:
    enabled: true
    storage_path: "workspace/traces"
    sample_rate: 1.0
  logging:
    level: "INFO"
    format: "json"
    file: "workspace/logs/agent.log"
    max_size_mb: 100
    backup_count: 7

# MCP: 一期预留框架，不接入外部 Server
mcp:
  enabled: false
  config_file: "config/mcp_servers.yaml"
```

---

## 6. 安全设计

### 6.1 Approval Flow（审批流）

所有配置在 `safety.approval_required_tools` 中的工具，执行前必须经过人工确认。审批请求通过当前会话通道发送，等待用户回复确认。超时未响应自动拒绝。

```python
async def _request_approval(
    self, tool_call: ToolCall, message: NormalizedMessage
) -> bool:
    approval_msg = (
        f"⚠️ 我需要执行以下操作，请确认：\n"
        f"工具: {tool_call.name}\n"
        f"参数: {json.dumps(tool_call.params, indent=2, ensure_ascii=False)}\n"
        f"回复 'y' 确认执行，其他内容取消。"
    )
    await self.gateway.send(message.session_id, approval_msg)
    response = await self.gateway.wait_for_reply(
        message.session_id, timeout=300
    )
    return response and response.body.strip().lower() in ("y", "yes", "确认", "好")
```

### 6.2 工具隔离

Shell 执行工具限制工作目录和超时时间（通过配置）。M-flow 数据目录（`mflow_data/`）与 Agent workspace 目录分离，防止 Agent 通过文件操作工具意外修改图谱数据。

### 6.3 Skill 安全

一期仅使用自行编写的 Skill，不开放第三方 Skill 安装。每个 Skill 文件在启用前需人工审查。

### 6.4 记忆安全

MEMORY.md 修改采用"下一轮生效"机制，防止 Agent 在单次会话中通过自我修改记忆绕过约束。M-flow 的 dataset 隔离确保不同类别的数据不交叉污染。

### 6.5 飞书通道安全

飞书 Adapter 仅处理来自已配置应用的事件。群聊模式下仅响应 @机器人 的消息，避免在群内产生不必要的干扰。飞书 APP_SECRET 通过环境变量注入，不写入配置文件。

---

## 7. 扩展预留（二期+）

以下模块在一期架构中已预留接口，不在一期实现范围内。

### 7.1 MCP 外部 Server 接入（二期）

扩展方式：在 `config/mcp_servers.yaml` 中添加 Server 配置，将 `agent.yaml` 中 `mcp.enabled` 设为 `true`。启动时 MCPDiscovery 自动发现并注册所有 Server 的工具到 ToolRegistry。Agent Core 代码无需改动。

优先接入的 MCP Server：Google Calendar（日程管理）、Gmail（邮件处理）、Notion（笔记/文档）、GitHub（代码仓库）。

### 7.2 Sub-agent 委派（二期）

扩展方式：在 ToolRegistry 中注册 `delegate_task` tool。该 tool 内部创建隔离上下文，复用同一套 ReAct loop 代码运行子 Agent，完成后只返回摘要给主 Agent。子 Agent 上下文随即回收。

关键设计点：子 Agent 可使用不同模型（Scout 用 Haiku，生成用 Opus）；可限制工具集；spawn 阈值为子任务预估输入超 10,000 tokens。

### 7.3 Plan-and-Execute（二期）

扩展方式：在 ToolRegistry 中注册 `plan_task` tool，接收任务描述、输出结构化步骤列表。主控 Agent 逐步执行每个步骤，过程中可随时 Replan。

### 7.4 Learning Loop（三期）

扩展方式：在 `_post_turn` 中增加条件判断——本轮满足条件（工具调用 ≥5、错误恢复、用户纠正）时，触发 Skill 提取流程，将有效工作流写成 SKILL.md。更新 Skill 优先 patch 而非全量重写。

### 7.5 更多飞书能力（持续）

扩展方向：支持飞书消息卡片（Interactive Card）做富文本回复和审批交互；支持语音消息（接收后调用 STT 转文本）；支持图片消息（调用 Vision 模型理解）；飞书群聊 Topic 隔离（不同话题独立 session）。

---

## 8. 一期实施里程碑

| 阶段 | 周期 | 交付物 | 验收标准 |
|------|------|--------|---------|
| **M1: 核心循环** | 第1-2周 | ReAct Loop + CLI Adapter + 3个内置工具 (file_read/write, shell_exec, web_search) + Provider Manager [v1.1] | 能通过 CLI 完成多轮工具调用对话；主 Provider 断开时自动切换备用 |
| **M2: 记忆基座** | 第3-4周 | SOUL.md / MEMORY.md 注入 + SQLite Session Archive + FTS5 检索 + Context Compaction + Session Manager [v1.1] | 跨会话记住用户偏好；长对话（50+轮）不崩溃；Session 可恢复 |
| **M3: M-flow 集成** | 第5-6周 | M-flow Bridge (异步写入+图路由检索) + recall_memory tool + LanceDB 本地存储 | 对话数据自动入库构建图谱；能回答跨会话因果推理问题 |
| **M4: Skill 系统 + MCP 预留** | 第7-8周 | Skill 按需加载 + 3个实用 Skill + MCP Client 框架 + Registry + Discovery（配置为空） | Skill 按需加载不膨胀上下文；MCP 框架代码完整可测试（mock Server） |
| **M5: 飞书接入 + 调度** | 第9-10周 | 飞书 Adapter (lark-oapi 长连接) + Gateway 长驻进程 + Heartbeat + Cron [v1.1] + Approval Flow | 飞书私聊/群聊与 Agent 交互；定时任务准时执行；高危操作需确认 |
| **M6: 稳定化** | 第11-12周 | 错误处理完善 + 观测性系统 [v1.1] + 更多 Skill 编写 + 日常使用打磨 | 连续一周日常使用不出严重问题；可查看 metrics 和 traces |

---

## 9. 关键设计决策记录

| 编号 | 决策 | 理由 | 备选方案 | 否决理由 |
|------|------|------|---------|---------|
| D01 | 自研核心循环，不用框架 | 单 Agent 场景，框架抽象是负担；需完全控制每一层 | LangGraph | 升级频繁、调试困难、抽象遮蔽行为 |
| D02 | 一期即集成 M-flow | 记忆质量取决于首日数据积累；延迟引入需全量重建图谱 | 先 SQLite FTS5 后加 | 数据迁移成本高，LanceDB 零运维 |
| D03 | M-flow 用 LanceDB 嵌入模式 | 零外部依赖、纯文件存储、极简基础设施 | Neo4j / PostgreSQL | 违反极简原则，个人规模不需要 |
| D04 | Python 而非 TypeScript | M-flow Python 生态；LLM SDK 支持最好 | TypeScript (OpenClaw 路线) | M-flow 集成需跨语言桥接 |
| D05 | MEMORY.md 修改下轮生效 | 防止单次会话自我修改记忆绕过约束 | 即时生效 | 安全风险：自我强化循环 |
| D06 | M-flow 异步入库 | 不阻塞响应；入库失败不影响主流程 | 同步入库 | 图谱构建含 LLM 调用，延迟高 |
| D07 | Session Archive 与 M-flow 并存 | FTS5 做时序查询更快；M-flow 做因果推理更准 | 只用 M-flow | 简单查询杀鸡用牛刀 |
| D08 | MCP 一期只做口子不接 Server | 降低一期复杂度；框架就位后二期扩展零代码改动 | 一期就接 Calendar/Gmail | 外部依赖增加调试成本，核心循环未稳定 |
| D09 | 飞书用 lark-oapi 长连接 | 无需公网 IP、内置鉴权、本地开发即可用 | Webhook 回调模式 | 需公网域名+HTTPS+防火墙配置 |
| D10 | 飞书作为默认通道 | 移动端随时可达；企业协作场景适配 | Telegram / 微信 | 飞书开放平台 API 最完善、SDK 质量高 |
| **D11** | **[v1.1] Provider 抽象层** | 支持多 LLM 厂商切换、降低锁定风险 | 硬编码 Anthropic/OpenAI | 无法接入 DeepSeek/本地模型等 |
| **D12** | **[v1.1] Heartbeat + Cron 双调度** | Heartbeat 做轮询检查，Cron 做精确时间任务 | 只用 Heartbeat | 无法表达"每天8点"这样的精确时间 |
| **D13** | **[v1.1] 自研观测性系统** | 轻量、无外部依赖、文件持久化 | Prometheus + Jaeger | 个人规模不需要重型监控栈 |
| **D14** | **[v1.1] Session 持久化到 SQLite** | 进程重启可恢复、与会话归档复用同一数据库 | Redis/内存 | 进程重启丢失会话状态 |

---

## v1.1 变更摘要

| 模块 | 原设计 (v1.0) | 更新后 (v1.1) |
|------|--------------|---------------|
| **LLM Provider** | 硬编码 Anthropic/OpenAI，固定槽位 | 抽象接口 + 动态注册 + 任意 OpenAI 兼容服务 |
| **观测性** | 无 | Metrics 收集 + Tracing + 结构化日志 |
| **Session 管理** | 简单内存字典 | 生命周期管理 + 持久化 + 恢复 + 群聊隔离 |
| **定时任务** | 只有 Heartbeat | Heartbeat（轮询）+ Cron（精确时间） |

---

> *文档结束。后续迭代内容（MCP Server 接入、Sub-agent、Plan-and-Execute、Learning Loop）将在二期设计文档中展开。*
