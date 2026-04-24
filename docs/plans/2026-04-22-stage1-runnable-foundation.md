# Stage One Runnable Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a runnable single-channel CLI agent that can load `SOUL.md` and `MEMORY.md`, call Anthropic through a preserved provider abstraction, execute a safe builtin tool set, persist conversation history to SQLite, and continue multi-turn conversations through a simplified session manager.

**Architecture:** Stage One is intentionally a complete vertical slice, not a partial scaffold. We will ship one fully runnable path: `CLI Adapter -> Agent Core -> Provider Manager -> Tool Registry -> Memory/Session persistence`, while explicitly excluding Feishu, approval flow, compaction, M-flow, observability, and multi-provider fallback. To keep Stage One independently runnable, the builtin tool set is reduced to `file_read`, `file_write`, `memory_write`, `search_sessions`, and `read_skill`; `shell_exec` and `web_search` are deferred until later phases.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `anthropic`, `PyYAML`, stdlib `sqlite3`, stdlib `pathlib`, dataclasses

---

## Stage One Delivery Contract

Stage One is only complete if all of the following are true:

- `uv run pytest` passes locally.
- `uv run python -m agent.cli.main --config config/agent.yaml` starts an interactive CLI session.
- A user prompt can produce either a plain assistant reply or a tool-using reply.
- `workspace/sessions.db` is created automatically and contains archived turns.
- `SOUL.md`, `MEMORY.md`, and the Skill index are injected into the model context.
- Restarting the CLI process is not required for normal multi-turn chat inside the same run.

## Non-Goals For Stage One

- No Feishu adapter
- No approval flow
- No compaction
- No M-flow ingestion or recall
- No Heartbeat or Cron
- No observability stack
- No OpenAI-compatible provider or fallback routing
- No group-chat isolation

## Suggested Commit Cadence

- `chore: bootstrap stage1 project skeleton`
- `feat: add simplified session persistence`
- `feat: add always-on memory and sqlite archive`
- `feat: add safe builtin tool registry`
- `feat: add skill loader`
- `feat: add anthropic provider manager`
- `feat: add react loop and context assembly`
- `feat: add runnable cli entrypoint`

### Task 1: Bootstrap The Runnable Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `agent/__init__.py`
- Create: `agent/config/__init__.py`
- Create: `agent/config/models.py`
- Create: `agent/config/loader.py`
- Create: `config/agent.yaml`
- Create: `config/providers.yaml`
- Create: `workspace/SOUL.md`
- Create: `workspace/MEMORY.md`
- Create: `workspace/skills/.gitkeep`
- Test: `tests/config/test_loader.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from agent.config.loader import load_settings


def test_load_settings_resolves_workspace_and_default_provider(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()

    (config_dir / "agent.yaml").write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ../workspace\n"
        "  max_iterations: 8\n"
        "providers:\n"
        "  config_file: providers.yaml\n"
        "  default_primary: claude-sonnet\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  - name: claude-sonnet\n"
        "    type: anthropic\n"
        "    model: claude-sonnet-4-20250514\n"
        "    api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "agent.yaml")

    assert settings.agent.name == "Yi Min"
    assert settings.agent.workspace_dir == workspace_dir.resolve()
    assert settings.providers.default_primary == "claude-sonnet"
    assert settings.providers.items[0].model == "claude-sonnet-4-20250514"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_loader.py -v`
Expected: FAIL with `ModuleNotFoundError` or missing `load_settings`.

**Step 3: Write minimal implementation**

```python
# agent/config/models.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AgentSettings:
    name: str
    workspace_dir: Path
    max_iterations: int


@dataclass(slots=True)
class ProviderItem:
    name: str
    type: str
    model: str
    api_key_env: str


@dataclass(slots=True)
class ProviderSettings:
    config_file: Path
    default_primary: str
    items: list[ProviderItem]


@dataclass(slots=True)
class Settings:
    agent: AgentSettings
    providers: ProviderSettings
```

```python
# agent/config/loader.py
from pathlib import Path
import yaml

from agent.config.models import AgentSettings, ProviderItem, ProviderSettings, Settings


def load_settings(agent_config_path: Path) -> Settings:
    agent_config_path = Path(agent_config_path).resolve()
    root = agent_config_path.parent
    raw = yaml.safe_load(agent_config_path.read_text(encoding="utf-8"))
    provider_path = (root / raw["providers"]["config_file"]).resolve()
    provider_raw = yaml.safe_load(provider_path.read_text(encoding="utf-8"))
    return Settings(
        agent=AgentSettings(
            name=raw["agent"]["name"],
            workspace_dir=(root / raw["agent"]["workspace_dir"]).resolve(),
            max_iterations=raw["agent"]["max_iterations"],
        ),
        providers=ProviderSettings(
            config_file=provider_path,
            default_primary=raw["providers"]["default_primary"],
            items=[ProviderItem(**item) for item in provider_raw["providers"]],
        ),
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_loader.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml agent/config tests/config config workspace
git commit -m "chore: bootstrap stage1 project skeleton"
```

### Task 2: Add Simplified Session Models And Session Manager

**Files:**
- Create: `agent/session/__init__.py`
- Create: `agent/session/models.py`
- Create: `agent/session/manager.py`
- Test: `tests/session/test_manager.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from agent.session.manager import SessionManager


async def test_session_manager_reuses_active_session(tmp_path: Path) -> None:
    manager = SessionManager(db_path=tmp_path / "sessions.db")

    first = await manager.get_or_create("cli:default", channel="cli")
    second = await manager.get_or_create("cli:default", channel="cli")

    assert first is second
    assert first.metadata.session_id == "cli:default"
    assert first.metadata.channel == "cli"
    assert first.metadata.message_count == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/session/test_manager.py -v`
Expected: FAIL with missing `SessionManager`.

**Step 3: Write minimal implementation**

```python
# agent/session/models.py
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SessionMetadata:
    session_id: str
    channel: str
    created_at: datetime
    last_active_at: datetime
    message_count: int = 0


@dataclass(slots=True)
class Session:
    metadata: SessionMetadata
    history: list[dict] = field(default_factory=list)

    def append(self, message: dict) -> None:
        self.history.append(message)
        self.metadata.message_count += 1
        self.metadata.last_active_at = datetime.utcnow()
```

```python
# agent/session/manager.py
from datetime import datetime

from agent.session.models import Session, SessionMetadata


class SessionManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._active_sessions: dict[str, Session] = {}

    async def get_or_create(self, session_id: str, channel: str) -> Session:
        if session_id in self._active_sessions:
            return self._active_sessions[session_id]
        now = datetime.utcnow()
        session = Session(
            metadata=SessionMetadata(
                session_id=session_id,
                channel=channel,
                created_at=now,
                last_active_at=now,
            )
        )
        self._active_sessions[session_id] = session
        return session
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/session/test_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/session tests/session
git commit -m "feat: add simplified session persistence"
```

### Task 3: Add Always-On Memory And SQLite Session Archive

**Files:**
- Create: `agent/memory/__init__.py`
- Create: `agent/memory/always_on.py`
- Create: `agent/memory/session_archive.py`
- Modify: `agent/session/manager.py`
- Test: `tests/memory/test_always_on.py`
- Test: `tests/memory/test_session_archive.py`

**Step 1: Write the failing tests**

```python
from pathlib import Path

from agent.memory.always_on import AlwaysOnMemory


def test_always_on_memory_reads_soul_and_memory(tmp_path: Path) -> None:
    soul = tmp_path / "SOUL.md"
    memory = tmp_path / "MEMORY.md"
    soul.write_text("# Identity\nYi Min\n", encoding="utf-8")
    memory.write_text("# User Profile\n- prefers python\n", encoding="utf-8")

    store = AlwaysOnMemory(soul_file=soul, memory_file=memory)

    assert "Yi Min" in store.load_soul()
    assert "prefers python" in store.load_memory()
```

```python
from pathlib import Path

from agent.memory.session_archive import SessionArchive


def test_session_archive_can_write_and_search_turns(tmp_path: Path) -> None:
    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    archive.append_turn("cli:default", 0, "user", "请记住我喜欢 Python")
    archive.append_turn("cli:default", 1, "assistant", "收到，我会记住")

    rows = archive.search("Python", limit=5)

    assert len(rows) == 1
    assert rows[0]["role"] == "user"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/memory/test_always_on.py tests/memory/test_session_archive.py -v`
Expected: FAIL with missing memory classes.

**Step 3: Write minimal implementation**

```python
# agent/memory/always_on.py
from pathlib import Path


class AlwaysOnMemory:
    def __init__(self, soul_file: Path, memory_file: Path) -> None:
        self.soul_file = Path(soul_file)
        self.memory_file = Path(memory_file)

    def load_soul(self) -> str:
        return self.soul_file.read_text(encoding="utf-8")

    def load_memory(self) -> str:
        return self.memory_file.read_text(encoding="utf-8")

    def replace_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")
```

```python
# agent/memory/session_archive.py
import sqlite3


class SessionArchive:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "session_id TEXT, turn_index INTEGER, role TEXT, content TEXT, "
                "PRIMARY KEY (session_id, turn_index))"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
                "content, content='sessions', content_rowid='rowid')"
            )

    def append_turn(self, session_id: str, turn_index: int, role: str, content: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, turn_index, role, content)"
                " VALUES (?, ?, ?, ?)",
                (session_id, turn_index, role, content),
            )
            conn.execute("INSERT INTO sessions_fts(rowid, content) VALUES (last_insert_rowid(), ?)", (content,))

    def search(self, query: str, limit: int) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT s.session_id, s.turn_index, s.role, s.content "
                "FROM sessions_fts f JOIN sessions s ON s.rowid = f.rowid "
                "WHERE sessions_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(row) for row in rows]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_always_on.py tests/memory/test_session_archive.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/memory agent/session/manager.py tests/memory
git commit -m "feat: add always-on memory and sqlite archive"
```

### Task 4: Add The Safe Builtin Tool Registry

**Files:**
- Create: `agent/tools/__init__.py`
- Create: `agent/tools/models.py`
- Create: `agent/tools/registry.py`
- Create: `agent/tools/executor.py`
- Create: `agent/tools/builtin/__init__.py`
- Create: `agent/tools/builtin/file_ops.py`
- Create: `agent/tools/builtin/memory_tools.py`
- Create: `agent/tools/builtin/session_tools.py`
- Test: `tests/tools/test_registry.py`
- Test: `tests/tools/test_builtin_tools.py`

**Step 1: Write the failing tests**

```python
from agent.tools.registry import build_stage1_registry


def test_stage1_registry_exposes_expected_safe_tools(tmp_path) -> None:
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
    )

    assert set(registry.names()) == {
        "file_read",
        "file_write",
        "memory_write",
        "search_sessions",
        "read_skill",
    }
```

```python
from pathlib import Path

from agent.tools.builtin.file_ops import file_write, file_read


def test_file_write_and_read_are_workspace_scoped(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    file_write(tmp_path, "notes.txt", "hello")

    assert target.read_text(encoding="utf-8") == "hello"
    assert file_read(tmp_path, "notes.txt") == "hello"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tools/test_registry.py tests/tools/test_builtin_tools.py -v`
Expected: FAIL with missing tool registry symbols.

**Step 3: Write minimal implementation**

```python
# agent/tools/models.py
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., str]
```

```python
# agent/tools/registry.py
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]
```

```python
# agent/tools/builtin/file_ops.py
from pathlib import Path


def _resolve(workspace_dir: Path, relative_path: str) -> Path:
    target = (workspace_dir / relative_path).resolve()
    if workspace_dir.resolve() not in target.parents and target != workspace_dir.resolve():
        raise ValueError("Path escapes workspace")
    return target


def file_read(workspace_dir: Path, relative_path: str) -> str:
    return _resolve(workspace_dir, relative_path).read_text(encoding="utf-8")


def file_write(workspace_dir: Path, relative_path: str, content: str) -> str:
    target = _resolve(workspace_dir, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return "ok"
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tools/test_registry.py tests/tools/test_builtin_tools.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/tools tests/tools
git commit -m "feat: add safe builtin tool registry"
```

### Task 5: Add The Skill Loader

**Files:**
- Create: `agent/skills/__init__.py`
- Create: `agent/skills/loader.py`
- Test: `tests/skills/test_loader.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from agent.skills.loader import SkillLoader


def test_skill_loader_builds_index_and_reads_full_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "daily-briefing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: daily-briefing\ndescription: Generate daily briefing\n---\n# Daily Briefing\n",
        encoding="utf-8",
    )

    loader = SkillLoader(tmp_path)

    index = loader.get_index()
    full = loader.read_full("daily-briefing")

    assert "daily-briefing" in index
    assert "Generate daily briefing" in index
    assert "# Daily Briefing" in full
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/skills/test_loader.py -v`
Expected: FAIL with missing `SkillLoader`.

**Step 3: Write minimal implementation**

```python
from pathlib import Path


class SkillLoader:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = Path(skills_dir)

    def get_index(self) -> str:
        lines = ["Available Skills:"]
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            name = text.split("name:", 1)[1].splitlines()[0].strip()
            description = text.split("description:", 1)[1].splitlines()[0].strip()
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    def read_full(self, skill_name: str) -> str:
        return (self.skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/skills/test_loader.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/skills tests/skills
git commit -m "feat: add skill loader"
```

### Task 6: Add Anthropic Provider Abstraction And Manager

**Files:**
- Create: `agent/core/provider.py`
- Create: `agent/core/provider_manager.py`
- Create: `agent/providers/__init__.py`
- Create: `agent/providers/anthropic.py`
- Test: `tests/providers/test_provider_manager.py`

**Step 1: Write the failing test**

```python
from agent.core.provider import LLMRequest, LLMResponse, ProviderConfig
from agent.core.provider_manager import ProviderManager


class FakeProvider:
    def __init__(self, config):
        self.config = config

    async def initialize(self) -> None:
        return None

    async def call(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(type="text", text="pong", provider=self.config.name, model=self.config.model)


async def test_provider_manager_calls_registered_primary_provider() -> None:
    config = ProviderConfig(
        name="claude-sonnet",
        type="anthropic",
        model="claude-sonnet-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
    )
    manager = ProviderManager(provider_factories={"anthropic": FakeProvider})
    await manager.register(config, make_primary=True)

    response = await manager.call(LLMRequest(messages=[{"role": "user", "content": "ping"}]))

    assert response.text == "pong"
    assert response.provider == "claude-sonnet"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_provider_manager.py -v`
Expected: FAIL with missing provider abstractions.

**Step 3: Write minimal implementation**

```python
# agent/core/provider.py
from dataclasses import dataclass, field


@dataclass(slots=True)
class ProviderConfig:
    name: str
    type: str
    model: str
    api_key_env: str


@dataclass(slots=True)
class LLMRequest:
    messages: list[dict]
    tools: list[dict] = field(default_factory=list)
    max_tokens: int | None = None


@dataclass(slots=True)
class LLMResponse:
    type: str
    text: str | None = None
    tool_calls: list[dict] | None = None
    provider: str = ""
    model: str = ""
```

```python
# agent/core/provider_manager.py
class ProviderManager:
    def __init__(self, provider_factories: dict[str, type]) -> None:
        self._provider_factories = provider_factories
        self._providers: dict[str, object] = {}
        self._primary: str | None = None

    async def register(self, config, make_primary: bool = False) -> None:
        provider = self._provider_factories[config.type](config)
        await provider.initialize()
        self._providers[config.name] = provider
        if make_primary or self._primary is None:
            self._primary = config.name

    async def call(self, request):
        return await self._providers[self._primary].call(request)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_provider_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/core/provider.py agent/core/provider_manager.py agent/providers tests/providers
git commit -m "feat: add anthropic provider manager"
```

### Task 7: Add Context Assembly And The Stage One ReAct Loop

**Files:**
- Create: `agent/core/context.py`
- Create: `agent/core/loop.py`
- Modify: `agent/session/manager.py`
- Create: `agent/gateway/normalizer.py`
- Test: `tests/core/test_context.py`
- Test: `tests/core/test_loop.py`

**Step 1: Write the failing tests**

```python
from agent.core.context import ContextAssembler


def test_context_assembler_includes_system_memory_skills_history_and_user_message() -> None:
    assembler = ContextAssembler(system_prompt="You are Yi Min.")

    context = assembler.assemble(
        soul_text="# Identity\nYi Min",
        memory_text="# User Profile\n- prefers python",
        skill_index="Available Skills:\n- daily-briefing: Generate daily briefing",
        history=[{"role": "assistant", "content": "你好"}],
        user_message="帮我总结今天做了什么",
    )

    assert context[0]["role"] == "system"
    assert "prefers python" in context[0]["content"]
    assert context[-1]["role"] == "user"
```

```python
from agent.core.loop import AgentCore
from agent.gateway.normalizer import NormalizedMessage


class FakeProviderManager:
    async def call(self, request):
        if any(msg["role"] == "tool" for msg in request.messages):
            return type("Resp", (), {"type": "text", "text": "已读取文件", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": None,
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "notes.txt"}}],
            },
        )()


def test_agent_core_can_execute_tool_then_finish(tmp_path) -> None:
    message = NormalizedMessage(
        message_id="1",
        session_id="cli:default",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="cli",
        metadata={},
    )
    core = AgentCore.build_for_test(tmp_path, FakeProviderManager())

    result = core.run_sync(message)

    assert result == "已读取文件"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_context.py tests/core/test_loop.py -v`
Expected: FAIL with missing assembler or loop implementation.

**Step 3: Write minimal implementation**

```python
# agent/core/context.py
class ContextAssembler:
    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt

    def assemble(self, soul_text, memory_text, skill_index, history, user_message) -> list[dict]:
        system = "\n\n".join(
            [
                self.system_prompt,
                "[SOUL.md]",
                soul_text,
                "[MEMORY.md]",
                memory_text,
                "[SKILL INDEX]",
                skill_index,
            ]
        )
        return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_message}]
```

```python
# agent/core/loop.py
class AgentCore:
    async def run(self, message):
        session = await self.session_manager.get_or_create(message.session_id, channel=message.channel)
        context = self.context_assembler.assemble(
            soul_text=self.always_on.load_soul(),
            memory_text=self.always_on.load_memory(),
            skill_index=self.skill_loader.get_index(),
            history=session.history,
            user_message=message.body,
        )
        for _ in range(self.max_iterations):
            response = await self.provider_manager.call(
                self.request_factory(messages=context, tools=self.tool_registry.get_schemas())
            )
            if response.type == "text":
                session.append({"role": "assistant", "content": response.text})
                self.archive.persist_session(session)
                return response.text
            for tool_call in response.tool_calls:
                result = self.tool_executor.execute(tool_call["name"], tool_call["input"])
                session.append({"role": "tool", "content": result})
                context.append({"role": "tool", "content": result})
        raise RuntimeError("max iterations exceeded")
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_context.py tests/core/test_loop.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/core agent/gateway tests/core
git commit -m "feat: add react loop and context assembly"
```

### Task 8: Add The Runnable CLI Entry Point And Stage Gate Verification

**Files:**
- Create: `agent/cli/__init__.py`
- Create: `agent/cli/main.py`
- Create: `agent/app.py`
- Modify: `README.md`
- Test: `tests/integration/test_cli_app.py`

**Step 1: Write the failing integration test**

```python
from pathlib import Path

from agent.app import build_app


def test_build_app_wires_a_runnable_cli_agent(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    skills = workspace / "skills"
    skills.mkdir(parents=True)
    (workspace / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (workspace / "MEMORY.md").write_text("# User Profile\n- prefers python\n", encoding="utf-8")
    (skills / "daily-briefing").mkdir()
    (skills / "daily-briefing" / "SKILL.md").write_text(
        "---\nname: daily-briefing\ndescription: Generate daily briefing\n---\n# Daily Briefing\n",
        encoding="utf-8",
    )

    app = build_app(config_path=Path("config/agent.yaml"), testing=True)

    reply = app.handle_text("你好", session_id="cli:default")

    assert isinstance(reply, str)
    assert reply
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_cli_app.py -v`
Expected: FAIL with missing app wiring or CLI entrypoint.

**Step 3: Write minimal implementation**

```python
# agent/app.py
class AgentApplication:
    def __init__(self, core) -> None:
        self.core = core

    def handle_text(self, text: str, session_id: str) -> str:
        message = NormalizedMessage(
            message_id=str(uuid4()),
            session_id=session_id,
            sender="cli-user",
            body=text,
            attachments=[],
            channel="cli",
            metadata={},
        )
        return asyncio.run(self.core.run(message))
```

```python
# agent/cli/main.py
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/agent.yaml")
    args = parser.parse_args()
    app = build_app(Path(args.config))
    print("Yi Min CLI is ready. Type 'exit' to quit.")
    while True:
        text = input("> ").strip()
        if text in {"exit", "quit"}:
            break
        if not text:
            continue
        print(app.handle_text(text, session_id="cli:default"))
```

**Step 4: Run the full verification suite**

Run: `uv run pytest -v`
Expected: PASS

Run: `uv run python -m agent.cli.main --config config/agent.yaml`
Expected: CLI starts, accepts input, returns a model response, and creates `workspace/sessions.db`.

Run: `uv run python - <<'PY'\nimport sqlite3\nconn = sqlite3.connect('workspace/sessions.db')\nprint(conn.execute('select count(*) from sessions').fetchone()[0])\nPY`
Expected: Printed count is greater than `0`.

**Step 5: Commit**

```bash
git add agent/cli agent/app.py README.md tests/integration
git commit -m "feat: add runnable cli entrypoint"
```

## Final Verification Checklist

- `uv sync`
- `uv run pytest -v`
- `uv run python -m agent.cli.main --config config/agent.yaml`
- In the CLI, send one normal message and one message that should trigger a safe tool
- Confirm `workspace/sessions.db` exists
- Confirm `search_sessions` can retrieve the just-created turn

## Execution Notes

- Do not add Feishu, approval flow, compaction, M-flow, observability, or fallback providers during Stage One.
- Keep files under 200 lines where practical; split helpers into domain-specific modules rather than generic utility buckets.
- Preserve the provider abstraction even though only Anthropic is enabled.
- Make every task end in a runnable, testable checkpoint; do not leave stubs that require Stage Two to become usable.


