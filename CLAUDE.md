# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run (local dev, no API keys needed)
uv run python -m agent.main --mode cli --testing

# Run modes
uv run python -m agent.main                          # default: all
uv run python -m agent.main --mode cli
uv run python -m agent.main --mode web
uv run python -m agent.main --mode gateway
uv run python -m agent.main --mode all

# Gateway with optional schedulers
uv run python -m agent.gateway.main
uv run python -m agent.gateway.main --enable-heartbeat --enable-cron

# Tests
uv run pytest -v
uv run pytest tests/tools/test_fitness_tools.py -v   # single file

# Linux systemd lifecycle (after install_linux.sh)
yimin start|stop|restart|status|logs
```

## Architecture

### Entry point flow

A message enters via CLI (`agent/cli/`), Web FastAPI (`agent/web/`), or Feishu (`agent/gateway/`) and is normalized into a `NormalizedMessage`. All paths converge at `AgentApplication` ([agent/app.py](agent/app.py)) which delegates to `AgentCore.run_events()` ([agent/core/loop.py](agent/core/loop.py)).

`AgentApplication` is assembled by `build_app_async()` / `build_channel_apps_async()` in [agent/app.py](agent/app.py). This function wires together every subsystem (provider, memory stores, tool registry, skill loader, scheduler hooks) by dependency injection — `AgentCore` owns none of them directly.

### ReAct loop (`agent/core/loop.py`)

Each turn:
1. **Tool visibility routing** — a cheap LLM call (no tools, max 24 tokens) classifies the user message into one of 7 domains: `general | fitness | bookkeeping | notes | scheduling | current_events | identity`. Only tools tagged `always` plus the matched domain are passed to the main model.
2. **Context assembly** (`agent/core/context.py`) — stacks: system prompt → SOUL.md (persona) → PROFILE.md (user profile) → mem0/local memory hits → skill index → history (last N turns, sanitized) → user message.
3. **LLM call** — streamed via `ProviderManager`. If tool calls come back, execute them and loop (max `max_iterations`, default 8). If plain text, archive and finish.
4. **Direct tool response** — scheduling tools (cron, reminder) short-circuit a second model call and return formatted text directly.
5. **Background tasks** — on final reply: memory extraction runs as `asyncio.create_task`, M-flow ingestion runs as `asyncio.create_task`. Neither blocks the response.

### Memory system (layered)

| Layer | Location | When used |
|---|---|---|
| `AlwaysOnMemory` | `SOUL.md` + `PROFILE.md` files | Every turn, always injected |
| `Mem0MemoryService` | Local Qdrant (`mem0_qdrant/`) + SQLite | Per-turn semantic search; extracted async after each reply |
| `MemoryStore` | `agent.db` SQLite | Fallback when mem0 unavailable |
| `MflowBridge` | LanceDB + Kuzu graph (`mflow_data/`) | Background ingestion of full turns |
| `SessionArchive` | `agent.db` SQLite | Full conversation history |

### Tool registry

[agent/tools/registry.py](agent/tools/registry.py) — `build_stage1_registry()` registers all builtin tools with visibility tags. Builtin tools live in [agent/tools/builtin/](agent/tools/builtin/): `file_ops`, `web_tools`, `ledger_tools`, `note_tools`, `fitness_tools`, `cron_tools`, `reminder_tools`, `shell_tools`, `identity_tools`, `memory_tools`, `session_tools`.

Tools requiring approval before execution: `assistant_identity_update`, `file_write`, `profile_core_update`, `profile_write`, `shell_exec` (when `requires_confirmation: true`).

### Workspace (auto-scaffolded on startup)

Every `AgentApplication` instance owns a `workspace_dir`. On first run, `_ensure_workspace_files()` creates:

- `SOUL.md` — agent persona (the "Silver Moon" character)
- `PROFILE.md` — user profile (migrated from legacy `MEMORY.md`)
- `HEARTBEAT.md` — periodic task spec for HeartbeatScheduler
- `CRON.yaml` / `REMINDERS.yaml` — scheduled jobs
- `agent.db` — SQLite (sessions, notes, ledger, memories, identity)
- `skills/` — SkillLoader reads SKILL.md files here; default skills are copied from [agent/skills/defaults/](agent/skills/defaults/)

`workspace/` is gitignored and is never committed.

### Configuration

- `config/agent.yaml` — local dev (workspace at `../workspace`, `providers.default_primary: qwen`)
- `config/agent.linux.yaml` — systemd deploy config
- `config/providers.yaml` — LLM provider definitions (name, model, `api_key_env`, `base_url`)
- `.env` (copy from `.env.example`) — secrets read as env vars

Provider secrets are never hardcoded; the config refers to env var names via `api_key_env`. The default provider `qwen` reads `OPENAI_API_KEY` (DashScope-compatible endpoint).

### Multi-channel mode

When `config/agent.yaml` has `channels.instances` with multiple entries, `build_channel_apps_async()` builds a separate `AgentApplication` per instance (each with its own workspace). In this mode, Heartbeat/Cron are disabled automatically.

### Feishu gateway

[agent/gateway/](agent/gateway/) — `GatewayServer` wraps a `FeishuAdapter` (lark-oapi long-connection). Feishu cards are rendered by [agent/gateway/feishu_cards.py](agent/gateway/feishu_cards.py) for structured responses (e.g. fitness summaries). Streaming placeholder replies are sent immediately while the ReAct loop runs.

### Observability

Langfuse tracing is configured via `observability.langfuse` in the agent YAML. Each `run_events()` call opens a trace; model calls and tool calls open child spans. Set `enabled: false` or leave keys unset to disable. ReAct debug logs also write to `workspace/logs/react.log` via `ReactTraceLogger`.

### M-flow / embedding

If `mflow.enabled: true`, the `MflowBridge` initializes LanceDB + Kuzu in `workspace/mflow_data/`. If you change `embedding.dimensions` or upgrade mflow, delete `mflow_data/` and rebuild — vector schema mismatches are not auto-migrated. DashScope `text-embedding-v4` requires `dimensions: 1024` and `batch_size: 10` (API limit).
