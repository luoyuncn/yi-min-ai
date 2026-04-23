# Yimin AI Agent 设计补充文档 v1.1

> **文档版本**: v1.1 补充  
> **日期**: 2026-04-22  
> **状态**: 对 v1.0 的增强设计  
> **变更范围**: LLM Provider 抽象层、观测性设计、Session 管理、Cron 定时任务

---

## 目录

- [S1. LLM Provider 抽象层设计](#s1-llm-provider-抽象层设计)
- [S2. 观测性设计](#s2-观测性设计)
- [S3. Session 管理](#s3-session-管理)
- [S4. Cron 定时任务（补充 Heartbeat）](#s4-cron-定时任务补充-heartbeat)
- [S5. 配置文件更新](#s5-配置文件更新)

---

## S1. LLM Provider 抽象层设计

### S1.1 问题分析

原设计中 Provider 层存在以下不足：
1. 硬编码 Anthropic 和 OpenAI 两个 Provider
2. 配置只有 `primary`/`fallback`/`compaction` 三个固定槽位
3. 无法方便接入 OpenAI API 兼容服务（DeepSeek, Moonshot, Groq, Together AI, Azure OpenAI, 本地 Ollama 等）

### S1.2 设计目标

- 统一的 Provider 抽象接口，支持任意数量的 Provider 注册
- 所有 OpenAI API 兼容服务通过同一个适配器接入
- 灵活的路由策略：按用途（primary/fallback/compaction）、按成本、按延迟
- 运行时动态切换 Provider（不重启）

### S1.3 Provider 抽象接口

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

### S1.4 Provider 实现

#### Anthropic Provider

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
        # 使用 Anthropic 的 token 计数 API
        return self._client.count_tokens(text)

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list]:
        """OpenAI 消息格式 → Anthropic 格式"""
        system_prompt = ""
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
            else:
                converted.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        return system_prompt.strip(), converted
```

#### OpenAI Compatible Provider

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
        # 使用 tiktoken 估算
        import tiktoken
        try:
            enc = tiktoken.encoding_for_model(self.config.model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
```

### S1.5 Provider Manager（路由管理器）

```python
from enum import Enum
from typing import Callable

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
        # 根据 type 选择实现类
        provider_class = self._get_provider_class(config.type)
        provider = provider_class(config)

        # 初始化并验证连接
        try:
            await provider.initialize()
            self._health_status[config.name] = True
        except Exception as e:
            logger.warning(f"Provider {config.name} initialization failed: {e}")
            self._health_status[config.name] = False
            return

        self._providers[config.name] = provider

        # 注册角色映射
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
                # 标记不健康，后台任务会定期恢复检查
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

        # 优先使用指定的 provider
        if preferred and preferred in providers:
            providers.remove(preferred)
            providers.insert(0, preferred)

        # 如果主角色没有可用的，尝试 fallback
        if not providers and role == ProviderRole.PRIMARY:
            providers = self._role_mapping.get(ProviderRole.FALLBACK, [])

        return providers

    async def health_check(self) -> dict[str, bool]:
        """健康检查所有 Provider"""
        for name, provider in self._providers.items():
            try:
                # 发送一个最小请求测试连通性
                await provider.call(LLMRequest(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ))
                self._health_status[name] = True
            except Exception:
                self._health_status[name] = False
        return self._health_status.copy()

    def get_provider(self, name: str) -> Optional[LLMProvider]:
        """获取指定 Provider"""
        return self._providers.get(name)

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

### S1.6 配置文件示例

```yaml
# config/providers.yaml
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

---

## S2. 观测性设计

### S2.1 设计目标

- **Metrics**: 收集关键指标（token 使用量、延迟、成功率、成本）
- **Tracing**: 追踪单条消息的完整处理链路
- **Logging**: 结构化日志，敏感信息脱敏

### S2.2 Metrics 收集

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

### S2.3 Tracing（链路追踪）

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

# 使用示例（在 Agent Core 中）
async def run(self, message: NormalizedMessage) -> str:
    trace = tracer.start_trace(message.session_id, message.message_id)

    try:
        # 上下文组装
        with trace.start_span("context_assembly") as span:
            context = self.context_assembly.assemble(session, message)
            span.set_attribute("context_tokens", self._count_tokens(context))

        # ReAct Loop
        for iteration in range(max_iterations):
            with trace.start_span(f"react_iteration_{iteration}") as span:
                # LLM 调用
                with trace.start_span("llm_call") as llm_span:
                    response = await self.provider.call(...)
                    llm_span.set_attribute("provider", response.provider)
                    llm_span.set_attribute("latency_ms", response.latency_ms)

                if response.type == "tool_calls":
                    for tc in response.tool_calls:
                        with trace.start_span(f"tool_{tc.name}") as tool_span:
                            result = await self.tool_executor.execute(tc)
                            tool_span.set_attribute("success", not result.error)

        return response.text

    finally:
        tracer.end_trace()
```

### S2.4 结构化日志

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
logger.info("LLM call completed", provider="claude-sonnet", latency_ms=1234, tokens=500)
```

### S2.5 观测性配置

```yaml
# config/agent.yaml 新增
observability:
  metrics:
    enabled: true
    reset_interval: "1h"         # 指标重置间隔
    export_path: "workspace/metrics"

  tracing:
    enabled: true
    storage_path: "workspace/traces"
    sample_rate: 1.0             # 1.0 = 100% 采样

  logging:
    level: "INFO"
    format: "json"               # "json" | "text"
    file: "workspace/logs/agent.log"
    max_size_mb: 100
    backup_count: 7
    sensitive_keys:              # 额外的敏感字段
      - "user_email"
      - "phone"
```

---

## S3. Session 管理

### S3.1 设计目标

- Session 生命周期管理（创建、活跃、过期、归档）
- 进程重启后 Session 恢复
- 群聊场景下的多用户隔离
- Session 元数据持久化

### S3.2 Session 数据模型

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

### S3.3 Session Manager

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
        for i, msg in enumerate(session.history[-10:]):  # 只持久化最近 10 条
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

    async def get_session_info(self, session_id: str) -> Optional[dict]:
        """获取 Session 信息（调试用）"""
        if session_id in self._active_sessions:
            session = self._active_sessions[session_id]
            return {
                "session_id": session.metadata.session_id,
                "state": session.metadata.state.value,
                "message_count": session.metadata.message_count,
                "last_active": session.metadata.last_active_at.isoformat(),
                "in_memory": True,
            }
        return None
```

### S3.4 群聊多用户隔离

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
            # 完整实现需要 LLM 判断话题边界
            return f"group:{chat_id}"

        return f"group:{chat_id}"
```

### S3.5 Session 清理调度

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

---

## S4. Cron 定时任务（补充 Heartbeat）

### S4.1 Heartbeat vs Cron 的区别

| 特性 | Heartbeat | Cron |
|------|-----------|------|
| 触发方式 | 固定间隔轮询 | 精确时间点 |
| 适用场景 | 检查是否有事做 | 在特定时间执行特定任务 |
| 任务定义 | HEARTBEAT.md（自然语言） | CRON.yaml（结构化配置） |
| 典型用例 | 检查 inbox、待办跟进 | 每天 8 点发简报、每周五生成周报 |

### S4.2 Cron 配置格式

```yaml
# workspace/CRON.yaml
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

### S4.3 Cron 调度器实现

```python
from croniter import croniter
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Callable
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
                # 构造一个使用 skill 的消息
                prompt = f"请使用 {action['skill']} skill 执行任务。"
                result = await self._run_agent(prompt, task)

            elif action["type"] == "prompt":
                result = await self._run_agent(action["prompt"], task)

            elif action["type"] == "tool":
                # 直接调用工具（简化版，实际应通过 Agent）
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

---

## S5. 配置文件更新

### S5.1 更新后的 agent.yaml

```yaml
agent:
  name: "Atlas"
  workspace_dir: "./workspace"
  max_iterations: 25
  compaction_reserve: 4096

# 新增：Provider 配置改为引用独立文件
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
      default_channel: true
      group_chat_mode: "mention"
      # 新增：群聊 Session 隔离策略
      group_session_strategy: "shared"  # "shared" | "per_user" | "per_topic"

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

# 新增：Session 管理配置
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

# 新增：Cron 定时任务
cron:
  enabled: true
  config_file: "workspace/CRON.yaml"

safety:
  approval_required_tools:
    - "shell_exec"
    - "file_delete"
  approval_timeout_seconds: 300

# 新增：观测性配置
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

mcp:
  enabled: false
  config_file: "config/mcp_servers.yaml"
```

---

## 变更摘要

| 模块 | 原设计 | 更新后 |
|------|--------|--------|
| **LLM Provider** | 硬编码 Anthropic/OpenAI，固定槽位 | 抽象接口 + 动态注册 + 任意 OpenAI 兼容服务 |
| **观测性** | 无 | Metrics 收集 + Tracing + 结构化日志 |
| **Session 管理** | 简单内存字典 | 生命周期管理 + 持久化 + 恢复 + 群聊隔离 |
| **定时任务** | 只有 Heartbeat | Heartbeat（轮询）+ Cron（精确时间） |

---

> *本文档为 v1.0 设计的补充，应与主设计文档一起阅读。*
