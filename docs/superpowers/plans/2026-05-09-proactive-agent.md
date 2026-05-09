# Proactive Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ProactiveScheduler that wakes the agent at random intervals, gives it free time with full tool access, and forwards its response to Feishu only when it chooses to send one.

**Architecture:** A new `ProactiveScheduler` class wakes the agent at random intervals using `random.uniform(min, max)`. It builds a free-time trigger message with `sender="proactive"`, calls the existing `AgentCore.run()` unchanged, and sends the response to Feishu via gateway if the response does not contain `[不打扰]`. The loop.py domain router is bypassed for `sender="proactive"` so the agent gets all registered tools.

**Tech Stack:** Python asyncio, existing NormalizedMessage / AgentCore / GatewayServer plumbing, YAML config, pytest-asyncio.

---

## File Map

| File | Change |
|------|--------|
| `agent/config/models.py` | Add `ProactiveSettings` dataclass, add field to `Settings` |
| `agent/config/loader.py` | Parse `proactive` YAML section, pass to `Settings(...)` |
| `agent/scheduler/proactive.py` | **New** — ProactiveScheduler implementation |
| `agent/scheduler/__init__.py` | Export `ProactiveScheduler` |
| `agent/core/loop.py` | Bypass domain router when `message.sender == "proactive"` |
| `agent/gateway/main.py` | Add `--enable-proactive` flag, start/stop scheduler |
| `config/agent.yaml` | Add `proactive` config block |
| `tests/scheduler/test_proactive.py` | **New** — unit tests for ProactiveScheduler |

---

## Task 1: ProactiveSettings config model

**Files:**
- Modify: `agent/config/models.py`
- Modify: `agent/config/loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_proactive.py
from pathlib import Path
from agent.config.loader import load_settings


def test_load_settings_parses_proactive_section(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen-max\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n"
        "proactive:\n"
        "  enabled: true\n"
        "  min_interval_minutes: 15\n"
        "  max_interval_minutes: 60\n"
        "  quiet_hours: [0, 1, 2, 3]\n"
        "  session_id: oc_test123\n"
        "  channel: feishu\n",
        encoding="utf-8",
    )

    settings = load_settings(agent_yaml)

    assert settings.proactive is not None
    assert settings.proactive.enabled is True
    assert settings.proactive.min_interval_minutes == 15
    assert settings.proactive.max_interval_minutes == 60
    assert settings.proactive.quiet_hours == [0, 1, 2, 3]
    assert settings.proactive.session_id == "oc_test123"
    assert settings.proactive.channel == "feishu"


def test_load_settings_proactive_defaults_to_none(tmp_path: Path) -> None:
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        "providers:\n"
        "  - name: qwen\n"
        "    type: openai\n"
        "    model: qwen-max\n"
        "    api_key_env: OPENAI_API_KEY\n",
        encoding="utf-8",
    )
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: qwen\n",
        encoding="utf-8",
    )

    settings = load_settings(agent_yaml)

    assert settings.proactive is None
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/test_config_proactive.py -v
```
Expected: `AttributeError: 'Settings' object has no attribute 'proactive'`

- [ ] **Step 3: Add ProactiveSettings to models.py**

In `agent/config/models.py`, add after the `ToolSettings` dataclass (around line 116):

```python
@dataclass(slots=True)
class ProactiveSettings:
    """主动性调度配置。"""

    enabled: bool = False
    min_interval_minutes: int = 20
    max_interval_minutes: int = 90
    quiet_hours: list[int] | None = None
    session_id: str = ""
    channel: str = "feishu"
    channel_instance: str = "default"
```

Add `proactive` field to `Settings` dataclass (after `observability`):

```python
@dataclass(slots=True)
class Settings:
    agent: AgentSettings
    providers: ProviderSettings
    channels: ChannelSettings | None = None
    mem0: Mem0Settings | None = None
    tools: ToolSettings | None = None
    observability: ObservabilitySettings | None = None
    proactive: ProactiveSettings | None = None
```

- [ ] **Step 4: Add parser to loader.py**

In `agent/config/loader.py`, add this function after `_build_tool_settings`:

```python
def _build_proactive_settings(data: dict | None) -> "ProactiveSettings | None":
    if data is None:
        return None
    from agent.config.models import ProactiveSettings
    quiet_hours_raw = data.get("quiet_hours")
    quiet_hours = list(quiet_hours_raw) if isinstance(quiet_hours_raw, list) else None
    return ProactiveSettings(
        enabled=_optional_bool_with_default(data, "enabled", False),
        min_interval_minutes=_optional_int(data, "min_interval_minutes") or 20,
        max_interval_minutes=_optional_int(data, "max_interval_minutes") or 90,
        quiet_hours=quiet_hours,
        session_id=_optional_str(data, "session_id") or "",
        channel=_optional_str(data, "channel") or "feishu",
        channel_instance=_optional_str(data, "channel_instance") or "default",
    )
```

Wire it into `load_settings()` — update the `return Settings(...)` call (loader.py line 78-94):

```python
    return Settings(
        agent=AgentSettings(
            name=_require_str(agent_section, "name", "agent"),
            workspace_dir=_resolve_agent_workspace_dir(agent_section, config_dir=config_dir, channels=channels),
            max_iterations=_require_int(agent_section, "max_iterations", "agent"),
            context_history_turns=_optional_int(agent_section, "context_history_turns") or 10,
        ),
        providers=ProviderSettings(
            config_file=provider_path,
            default_primary=default_primary,
            items=provider_items,
        ),
        channels=channels,
        mem0=_build_mem0_settings(_optional_mapping(raw, "mem0"), config_dir=config_dir),
        tools=_build_tool_settings(_optional_mapping(raw, "tools")),
        observability=_build_observability_settings(_optional_mapping(raw, "observability")),
        proactive=_build_proactive_settings(_optional_mapping(raw, "proactive")),
    )
```

Also add `ProactiveSettings` to the import in `loader.py`:

```python
from agent.config.models import (
    AgentSettings,
    ChannelInstanceSettings,
    ChannelSettings,
    LangfuseSettings,
    Mem0EmbeddingSettings,
    Mem0Settings,
    ObservabilitySettings,
    ProactiveSettings,
    ProviderConfigItem,
    ProviderSettings,
    Settings,
    ShellToolSettings,
    ToolSettings,
)
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_config_proactive.py -v
```
Expected: 2 tests PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```
uv run pytest -v
```
Expected: all previously passing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add agent/config/models.py agent/config/loader.py tests/test_config_proactive.py
git commit -m "feat(config): add ProactiveSettings model and loader"
```

---

## Task 2: ProactiveScheduler

**Files:**
- Create: `agent/scheduler/proactive.py`
- Create: `tests/scheduler/test_proactive.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_proactive.py
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent.scheduler.proactive import ProactiveScheduler


class CapturingGateway:
    def __init__(self):
        self.sent = []

    async def send_to_channel(self, adapter_id, session_id, content):
        self.sent.append((adapter_id, session_id, content))


class CapturingCore:
    def __init__(self, response="[不打扰]"):
        self.messages = []
        self.response = response

    async def run(self, message):
        self.messages.append(message)
        return self.response


@pytest.mark.asyncio
async def test_skip_signal_suppresses_send() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    assert len(core.messages) == 1
    assert len(gateway.sent) == 0


@pytest.mark.asyncio
async def test_content_response_sends_to_gateway() -> None:
    core = CapturingCore(response="今天发现一件有趣的事！")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(
        core, gateway, session_id="oc_test", channel_instance="feishu"
    )

    await scheduler._execute_proactive()

    assert len(gateway.sent) == 1
    assert gateway.sent[0] == ("feishu", "oc_test", "今天发现一件有趣的事！")
    assert scheduler._send_count == 1


@pytest.mark.asyncio
async def test_empty_response_suppresses_send() -> None:
    core = CapturingCore(response="")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    assert len(gateway.sent) == 0


def test_quiet_hour_detection() -> None:
    scheduler = ProactiveScheduler(
        CapturingCore(), CapturingGateway(), quiet_hours=[0, 1, 2, 3], session_id=""
    )
    mock_now = MagicMock()
    mock_now.hour = 2
    with patch("agent.scheduler.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        assert scheduler._is_quiet_hour() is True

    mock_now.hour = 10
    with patch("agent.scheduler.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = mock_now
        assert scheduler._is_quiet_hour() is False


def test_daily_counter_resets_on_new_day() -> None:
    scheduler = ProactiveScheduler(
        CapturingCore(), CapturingGateway(), session_id=""
    )
    scheduler._send_count = 5
    scheduler._send_count_date = date.today() - timedelta(days=1)

    count = scheduler._today_send_count()

    assert count == 0
    assert scheduler._send_count_date == date.today()


@pytest.mark.asyncio
async def test_trigger_message_has_sender_proactive() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")

    await scheduler._execute_proactive()

    msg = core.messages[0]
    assert msg.sender == "proactive"
    assert "[自由时间]" in msg.body
    assert "[不打扰]" in msg.body


@pytest.mark.asyncio
async def test_send_count_injected_in_trigger_message() -> None:
    core = CapturingCore(response="[不打扰]")
    gateway = CapturingGateway()
    scheduler = ProactiveScheduler(core, gateway, session_id="oc_test")
    scheduler._send_count = 3

    await scheduler._execute_proactive()

    assert "3 次" in core.messages[0].body
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/scheduler/test_proactive.py -v
```
Expected: `ModuleNotFoundError: No module named 'agent.scheduler.proactive'`

- [ ] **Step 3: Create agent/scheduler/proactive.py**

```python
"""主动性调度器 - 随机间隔唤醒 agent，自由探索后决定是否发消息"""

import asyncio
import logging
import random
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from agent.gateway.normalizer import NormalizedMessage

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_SKIP_SIGNAL = "[不打扰]"


class ProactiveScheduler:
    """随机间隔唤醒 agent，让它自由探索，完成后决定是否发消息。"""

    def __init__(
        self,
        agent_core,
        gateway,
        *,
        min_interval_minutes: int = 20,
        max_interval_minutes: int = 90,
        quiet_hours: list[int] | None = None,
        session_id: str = "",
        channel_instance: str = "feishu",
    ):
        self.agent_core = agent_core
        self.gateway = gateway
        self.min_interval = min_interval_minutes * 60
        self.max_interval = max_interval_minutes * 60
        self.quiet_hours = set(quiet_hours or [])
        self.session_id = session_id
        self.channel_instance = channel_instance
        self._running = False
        self._task: asyncio.Task | None = None
        self._send_count: int = 0
        self._send_count_date: date = date.today()

    async def start(self) -> None:
        if self._running:
            logger.warning("Proactive scheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(
            "Proactive scheduler started (interval: %d-%dmin)",
            self.min_interval // 60,
            self.max_interval // 60,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Proactive scheduler stopped")

    def _today_send_count(self) -> int:
        today = date.today()
        if today != self._send_count_date:
            self._send_count = 0
            self._send_count_date = today
        return self._send_count

    def _is_quiet_hour(self) -> bool:
        if not self.quiet_hours:
            return False
        return datetime.now(_CST).hour in self.quiet_hours

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                interval = random.uniform(self.min_interval, self.max_interval)
                await asyncio.sleep(interval)
                if not self._running:
                    break
                if self._is_quiet_hour():
                    logger.debug("Proactive: quiet hour, skipping")
                    continue
                await self._execute_proactive()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Proactive execution error: %s", e, exc_info=True)

    async def _execute_proactive(self) -> None:
        send_count = self._today_send_count()
        now = datetime.now(_CST)
        weekday = _WEEKDAYS[now.weekday()]
        body = (
            f"[自由时间] 现在是 {weekday} {now.strftime('%H:%M')}，你有一些空闲。\n"
            f"今天已经主动联系过用户 {send_count} 次。\n\n"
            f"做任何你想做的事——搜索感兴趣的内容、回顾用户最近的状态、\n"
            f"或者只是想想有没有什么值得分享的。\n\n"
            f"完成之后：\n"
            f"- 如果有值得告诉用户的，直接写出来\n"
            f"- 如果没有什么要说的，回复：{_SKIP_SIGNAL}"
        )
        message = NormalizedMessage(
            message_id=f"proactive-{uuid4()}",
            session_id="__proactive__",
            sender="proactive",
            body=body,
            channel="internal",
            channel_instance=self.channel_instance,
            metadata={"type": "proactive"},
            timestamp=datetime.now(timezone.utc),
        )
        logger.info("Executing proactive cycle...")
        try:
            result = await self.agent_core.run(message)
            if not result or _SKIP_SIGNAL in result:
                logger.debug("Proactive: agent chose not to send")
                return
            logger.info("Proactive: sending message (%d chars)", len(result))
            await self.gateway.send_to_channel(self.channel_instance, self.session_id, result)
            self._send_count += 1
        except Exception as e:
            logger.error("Proactive execution failed: %s", e, exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/scheduler/test_proactive.py -v
```
Expected: 7 tests PASS

- [ ] **Step 5: Run full suite**

```
uv run pytest -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent/scheduler/proactive.py tests/scheduler/test_proactive.py
git commit -m "feat(scheduler): add ProactiveScheduler with random interval wakeup"
```

---

## Task 3: Export ProactiveScheduler from scheduler package

**Files:**
- Modify: `agent/scheduler/__init__.py`

- [ ] **Step 1: Update __init__.py**

Replace the contents of `agent/scheduler/__init__.py`:

```python
"""调度模块 - Heartbeat、Cron、Reminder 和 Proactive"""

from agent.scheduler.heartbeat import HeartbeatScheduler
from agent.scheduler.cron import CronScheduler
from agent.scheduler.reminder import ReminderScheduler
from agent.scheduler.proactive import ProactiveScheduler

__all__ = ["HeartbeatScheduler", "CronScheduler", "ReminderScheduler", "ProactiveScheduler"]
```

- [ ] **Step 2: Verify import works**

```
uv run python -c "from agent.scheduler import ProactiveScheduler; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agent/scheduler/__init__.py
git commit -m "feat(scheduler): export ProactiveScheduler"
```

---

## Task 4: Bypass domain router for proactive sender

**Files:**
- Modify: `agent/core/loop.py`

- [ ] **Step 1: Write the failing test**

Add a new test file:

```python
# tests/core/test_proactive_routing.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_proactive_sender_gets_none_visibility_tags():
    """When sender is 'proactive', visibility_tags must be None (all tools)."""
    from agent.tools.registry import ToolRegistry
    from agent.tools.models import ToolDefinition

    registry = ToolRegistry()
    registry.register(ToolDefinition(name="tool_a", description="A", schema={}, handler=lambda: None, visibility_tags={"fitness"}))
    registry.register(ToolDefinition(name="tool_b", description="B", schema={}, handler=lambda: None, visibility_tags={"general"}))

    # None means all tools
    all_tools = registry.get_schemas(visibility_tags=None)
    assert len(all_tools) == 2

    # With a specific tag, only matching tools
    tagged_tools = registry.get_schemas(visibility_tags={"fitness"})
    assert len(tagged_tools) == 1
```

- [ ] **Step 2: Run to verify it passes (it tests existing behavior)**

```
uv run pytest tests/core/test_proactive_routing.py -v
```
Expected: PASS (this verifies the `None → all tools` invariant we depend on)

- [ ] **Step 3: Modify loop.py to bypass domain router**

In `agent/core/loop.py`, find the block starting around line 272:

```python
                tool_route, visibility_tags = await self._select_tool_visibility(
                    selected_history,
                    user_message=message.body,
                    thread_id=thread_id,
                    run_id=run_id,
                    channel=message.channel,
                )
```

Replace with:

```python
                if message.sender == "proactive":
                    tool_route = "proactive"
                    visibility_tags = None
                else:
                    tool_route, visibility_tags = await self._select_tool_visibility(
                        selected_history,
                        user_message=message.body,
                        thread_id=thread_id,
                        run_id=run_id,
                        channel=message.channel,
                    )
```

- [ ] **Step 4: Run full test suite**

```
uv run pytest -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add agent/core/loop.py tests/core/test_proactive_routing.py
git commit -m "feat(loop): bypass domain router for proactive sender, expose all tools"
```

---

## Task 5: Wire ProactiveScheduler into gateway/main.py

**Files:**
- Modify: `agent/gateway/main.py`

- [ ] **Step 1: Add import**

In `agent/gateway/main.py`, update the scheduler import line:

```python
from agent.scheduler import HeartbeatScheduler, CronScheduler, ReminderScheduler, ProactiveScheduler
```

- [ ] **Step 2: Add CLI option**

In the `@click.command()` block of `main()`, after `--enable-cron`:

```python
@click.option(
    "--enable-proactive/--no-proactive",
    default=False,
    help="是否启用主动性调度（默认禁用）",
)
```

Add `enable_proactive: bool` to the `main()` function signature and the `asyncio.run(run_server(...))` call.

Updated `main()` signature:

```python
def main(
    config: str,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    enable_proactive: bool,
    log_level: str,
):
```

Updated `asyncio.run(run_server(...))` call:

```python
    asyncio.run(run_server(
        config_path=Path(config),
        enable_feishu=enable_feishu,
        enable_heartbeat=enable_heartbeat,
        heartbeat_interval=heartbeat_interval,
        enable_cron=enable_cron,
        enable_proactive=enable_proactive,
        log_level=log_level,
    ))
```

- [ ] **Step 3: Add enable_proactive parameter to run_server and wire the scheduler**

Update `run_server()` signature to accept `enable_proactive: bool`:

```python
async def run_server(
    config_path: Path,
    enable_feishu: bool,
    enable_heartbeat: bool,
    heartbeat_interval: int,
    enable_cron: bool,
    enable_proactive: bool,
    log_level: str,
):
```

Add `proactive_scheduler = None` near the top of `run_server` where other schedulers are declared (after line 121):

```python
    proactive_scheduler = None
```

After the Cron/Reminder block (after line 233, before `# 7. 启动 Gateway 主循环`), add:

```python
        # 7. 启动主动性调度器（可选）
        if enable_proactive:
            proactive_cfg = settings.proactive
            if proactive_cfg and proactive_cfg.enabled:
                logger.info("启动主动性调度器")
                proactive_scheduler = ProactiveScheduler(
                    agent_core=default_app.core,
                    gateway=gateway,
                    min_interval_minutes=proactive_cfg.min_interval_minutes,
                    max_interval_minutes=proactive_cfg.max_interval_minutes,
                    quiet_hours=proactive_cfg.quiet_hours,
                    session_id=proactive_cfg.session_id,
                    channel_instance=_default_channel_instance(settings),
                )
                await proactive_scheduler.start()
                logger.info("✓ 主动性调度器已启动")
            else:
                logger.warning("--enable-proactive 已设置，但 config 中 proactive.enabled=false 或无配置，已跳过")
```

In the `finally` block, add cleanup before the gateway stop:

```python
        if proactive_scheduler:
            await proactive_scheduler.stop()
            logger.info("✓ 主动性调度器已停止")
```

- [ ] **Step 4: Update the docstring example in main()**

Add to the `\b 使用示例：` section in the docstring:

```
        # 启用飞书 + 主动性调度
        uv run python -m agent.gateway.main --enable-proactive
```

- [ ] **Step 5: Verify the module imports without error**

```
uv run python -c "from agent.gateway.main import run_server; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Run full test suite**

```
uv run pytest -v
```
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add agent/gateway/main.py
git commit -m "feat(gateway): add --enable-proactive flag to start ProactiveScheduler"
```

---

## Task 6: Add proactive config block to agent.yaml

**Files:**
- Modify: `config/agent.yaml`

- [ ] **Step 1: Add proactive block**

Append to `config/agent.yaml`:

```yaml
proactive:
  enabled: true
  min_interval_minutes: 20
  max_interval_minutes: 90
  quiet_hours: [0, 1, 2, 3, 4, 5, 6, 7]
  session_id: ""        # 填入飞书用户 open_id 或群 chat_id
  channel: "feishu"
```

- [ ] **Step 2: Verify config loads without error**

```
uv run python -c "
from agent.config.loader import load_settings
from pathlib import Path
s = load_settings(Path('config/agent.yaml'))
print('proactive enabled:', s.proactive.enabled if s.proactive else None)
"
```
Expected: `proactive enabled: True`

- [ ] **Step 3: Run full test suite one final time**

```
uv run pytest -v
```
Expected: all tests PASS

- [ ] **Step 4: Final commit**

```bash
git add config/agent.yaml
git commit -m "config: add proactive scheduler defaults to agent.yaml"
```

---

## Usage

After setting `session_id` in `config/agent.yaml`, start with:

```bash
uv run python -m agent.gateway.main --enable-proactive
```

The agent wakes up at random 20–90 minute intervals (skipping 0–7am), runs a full ReAct loop with all tools available, and sends a message to the configured Feishu session only when it decides the content is worth sharing.
