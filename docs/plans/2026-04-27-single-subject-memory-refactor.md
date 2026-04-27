# Single Subject Memory Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor Yi Min into one production Feishu channel backed by one clear agent subject, replace M-flow as the default memory path with a lightweight auditable memory system, and fix the current configuration, identity, session, and proactive-task reliability issues.

**Architecture:** Keep Gateway and AgentCore, but make the production runtime single-subject by default: one Feishu adapter, one workspace, one identity, one durable memory store, and one scheduler context. Introduce explicit identity boundaries for `channel`, `human/user`, and `agent/workspace` so group chats no longer pretend that chat equals person. Move long-term memory from passive tools into a small domain service that automatically extracts, stores, retrieves, and injects relevant memories each turn. M-flow becomes an optional experimental adapter, not part of the default boot path.

**Tech Stack:** Python 3.12, FastAPI/Gateway existing runtime, SQLite FTS5, YAML config, pytest, existing OpenAI-compatible provider layer.

---

## Non-Goals

- Do not migrate to LangGraph, Letta, Mem0, or Zep in this refactor.
- Do not keep multi-Feishu-bot production mode as the default.
- Do not build a complex knowledge graph.
- Do not auto-write `MEMORY.md` directly from arbitrary model output.

## Target Runtime Model

- `channel`: transport, for example `feishu`.
- `channel_instance`: physical connector instance. Default production value should be `feishu`.
- `subject`: the Yi Min identity and durable memory owner. Phase 1 has exactly one subject.
- `workspace`: local runtime/project asset directory for this subject. Future main/sub-agent work can introduce more workspaces intentionally.
- `thread`: conversation lane, normally `feishu:feishu:<chat_id>` for channel ordering and reply context.
- `human`: the actual speaker, derived from Feishu `sender.open_id` and recorded separately from thread so group chats can distinguish people.
- `conversation_scope`: whether context should be private-human, group-thread, or mixed. Phase 1 records this metadata and keeps the existing thread lane; later work can split group memory by speaker safely.

## Problems This Plan Must Fix

### 1. Configuration Drift

Current default configs name a second bot as `feishu-ops`, but the runtime config uses `FEISHU_MIN_APP_ID` / `FEISHU_MIN_APP_SECRET` while `.env.example` documents `FEISHU_OPS_*`. That creates Linux deployments where one bot silently skips registration or behaves differently than the documentation. The fix is not just renaming variables; production defaults should stop declaring multiple Feishu bots at all.

Acceptance criteria:

- default configs declare one production Feishu channel;
- `.env.example`, README, and Linux docs agree on `FEISHU_APP_ID` / `FEISHU_APP_SECRET`;
- tests fail if `FEISHU_MIN_*` reappears in default configs or docs.

### 2. Multi-Bot Equals Multi-Subject Is The Wrong Default

The current design maps each Feishu robot to a separate workspace and therefore a separate `SOUL.md`, `MEMORY.md`, notes DB, ledger DB, and M-flow data directory. In practice `workspace-main` has real memory while `workspace-ops` is empty, so the same human can experience Yi Min as remembering or forgetting depending on which bot they talk to. Conversely, multiple humans inside one bot share the same subject memory without an explicit human boundary.

Acceptance criteria:

- default runtime has one workspace: `workspace/`;
- legacy `workspace-main` / `workspace-ops` are migration sources only, not default runtime targets;
- docs describe how to migrate existing `workspace-main` memory into `workspace/`;
- AgentCore records `sender` / human metadata for future per-human policy.

### 3. Session Boundary Cannot Be Only Chat

Feishu `chat_id` is a delivery lane, not a person. In private chat this mostly works, but in group chat it mixes multiple humans into one context. The existing `sender` is captured but not treated as a first-class identity by session, memory, or tool policy.

Acceptance criteria:

- `NormalizedMessage` carries stable `sender` and exposes an explicit human/user identity helper;
- session archive persists sender/human id for inbound messages;
- context assembly includes a small `[HUMAN CONTEXT]` block with current sender and chat type when available;
- memory extraction stores `source_sender_id` / human id.

### 4. Memory And Soul Are Passive, Not A Cognition Loop

`SOUL.md` and `MEMORY.md` are prompt files. Notes, session search, and M-flow are tools the model may or may not call. `MEMORY.md` is only updated by `memory_write`, and that tool replaces the entire file. M-flow ingestion is async, silent on failure, and retrieval requires the model to call `recall_memory`. This is why the system feels like it does not actively learn.

Acceptance criteria:

- successful turns trigger conservative memory extraction automatically;
- relevant memory is retrieved automatically before model calls;
- `MEMORY.md` remains a human-readable seed/profile file, not the primary mutable store;
- memory writes are auditable in SQLite with source thread/message/sender metadata.

### 5. Proactive Behavior Is Disabled By Multi-Runtime Mode

Heartbeat/Cron are disabled whenever `channels.instances` exists, even if there is only one configured instance. That removes exactly the proactive loop expected from Yi Min. The scheduler also has no per-subject routing model yet.

Acceptance criteria:

- one configured channel instance is treated as single-subject runtime and may run Heartbeat/Cron;
- two or more instances are advanced multi-runtime mode and still disable Heartbeat/Cron with an explicit warning;
- Heartbeat/Cron internal messages carry `channel_instance`, subject/workspace metadata, and a stable internal sender.

## Task 1: Lock The Production Config To One Subject

**Files:**
- Modify: `config/agent.yaml`
- Modify: `config/agent.linux.yaml`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `DEPLOY_LINUX.md`
- Test: `tests/config/test_loader.py`
- Test: `tests/deploy/test_linux.py`

**Step 1: Write config tests**

Add tests that assert:

- default local config has one workspace named `../workspace`;
- default local config has no `channels.instances`, or has exactly one instance named `feishu`;
- default Linux config has exactly one Feishu instance named `feishu`;
- default Linux config uses `FEISHU_APP_ID` / `FEISHU_APP_SECRET`;
- `.env.example` does not mention stale `FEISHU_MIN_*`;
- `.env.example` does not advertise multi-bot variables as the default setup;
- loading `config/agent.linux.yaml` resolves a single workspace.

Run:

```bash
uv run pytest tests/config/test_loader.py tests/deploy/test_linux.py -v
```

Expected: FAIL before implementation due current multi-instance defaults and `FEISHU_MIN_*` drift.

**Step 2: Update configs**

Change both default configs to one production channel:

```yaml
agent:
  name: "Yi Min"
  workspace_dir: "../workspace"
  max_iterations: 8

channels:
  instances:
    - name: "feishu"
      type: "feishu"
      workspace_dir: "../workspace"
      app_id_env: "FEISHU_APP_ID"
      app_secret_env: "FEISHU_APP_SECRET"
```

Recommended shape: remove `channels.instances` from local `agent.yaml` and keep only `agent.workspace_dir`; keep one instance in `agent.linux.yaml` for Gateway production.

**Step 3: Update docs**

Remove `FEISHU_MAIN_*`, `FEISHU_OPS_*`, `FEISHU_MIN_*`, `FEISHU_SALES_*` as default examples. Move multi-bot setup to a clearly marked legacy/advanced section, and explain that the default subject workspace is `workspace/`.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/config/test_loader.py tests/deploy/test_linux.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add config/agent.yaml config/agent.linux.yaml .env.example README.md DEPLOY_LINUX.md tests/config/test_loader.py tests/deploy/test_linux.py
git commit -m "refactor: default to single feishu subject"
```

## Task 2: Keep Multi Runtime Code But Mark It Advanced

**Files:**
- Modify: `agent/main.py`
- Modify: `agent/gateway/main.py`
- Modify: `agent/config/loader.py`
- Modify: `agent/scheduler/heartbeat.py`
- Modify: `agent/scheduler/cron.py`
- Test: `tests/gateway/test_server.py`
- Test: `tests/gateway/test_thread_key.py`
- Test: `tests/config/test_loader.py`
- Test: `tests/core/test_context.py`

**Step 1: Write behavior tests**

Assert:

- single configured Feishu instance is not treated as advanced multi-runtime mode;
- Heartbeat/Cron are not disabled for one instance;
- two or more instances still disable Heartbeat/Cron with a clear warning;
- thread keys remain stable: `feishu:feishu:<chat_id>`.
- internal Heartbeat/Cron messages include `channel_instance="feishu"` in single-subject runtime.

Run:

```bash
uv run pytest tests/gateway/test_server.py tests/gateway/test_thread_key.py tests/config/test_loader.py -v
```

Expected: FAIL where current logic uses `bool(settings.channels and settings.channels.instances)` as multi-runtime.

**Step 2: Introduce runtime count helper**

Add a small helper near entrypoints, for example:

```python
def _is_multi_runtime(settings) -> bool:
    return bool(settings.channels and len(settings.channels.instances) > 1)
```

Use it in `agent/main.py` and `agent/gateway/main.py`.

**Step 3: Improve startup warnings**

When more than one runtime exists, log:

```text
Multiple channel instances are advanced mode. Heartbeat/Cron are disabled until per-subject scheduling is implemented.
```

For one instance, do not warn.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/gateway/test_server.py tests/gateway/test_thread_key.py tests/config/test_loader.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/main.py agent/gateway/main.py agent/config/loader.py tests/gateway/test_server.py tests/gateway/test_thread_key.py tests/config/test_loader.py
git commit -m "fix: distinguish single channel from multi runtime"
```

## Task 3: Make M-flow Optional And Off By Default

**Files:**
- Modify: `pyproject.toml`
- Modify: `config/agent.yaml`
- Modify: `config/agent.linux.yaml`
- Modify: `agent/app.py`
- Modify: `agent/memory/mflow_bridge.py`
- Test: `tests/memory/test_mflow_bridge.py`
- Test: `tests/test_app.py`

**Step 1: Write tests**

Assert:

- default settings produce `mflow.enabled == False`;
- app boot does not import or initialize M-flow unless enabled;
- `recall_memory` is not registered when M-flow is disabled;
- enabling M-flow still works when dependency and keys are present or skips cleanly when unavailable.

Run:

```bash
uv run pytest tests/memory/test_mflow_bridge.py tests/test_app.py -v
```

Expected: FAIL because M-flow currently defaults on.

**Step 2: Move dependency to optional extra**

Change:

```toml
"mflow-ai>=0.3.6",
```

to an optional dependency group:

```toml
[project.optional-dependencies]
mflow = ["mflow-ai>=0.3.6"]
```

Keep tests tolerant when the extra is not installed.

**Step 3: Disable default config**

Set:

```yaml
mflow:
  enabled: false
```

Leave commented examples for enabling it experimentally.

**Step 4: Make app boot quiet**

In `agent/app.py`, return `None` immediately when `mflow.enabled` is false. Do not log warnings for disabled optional memory.

**Step 5: Verify**

Run:

```bash
uv run pytest tests/memory/test_mflow_bridge.py tests/test_app.py -v
uv run pytest tests/tools/test_registry.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add pyproject.toml uv.lock config/agent.yaml config/agent.linux.yaml agent/app.py agent/memory/mflow_bridge.py tests/memory/test_mflow_bridge.py tests/test_app.py tests/tools/test_registry.py
git commit -m "refactor: make mflow optional memory backend"
```

## Task 4: Add A First-Class Memory Store

**Files:**
- Create: `agent/memory/memory_store.py`
- Modify: `agent/memory/__init__.py`
- Test: `tests/memory/test_memory_store.py`

**Step 1: Write tests**

Cover:

- add memory item;
- search by text;
- list by kind;
- mark obsolete;
- merge duplicate through replacement pointer;
- source thread/message metadata persists.
- source sender/human metadata persists.

Suggested schema:

```sql
CREATE TABLE memory_items (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL DEFAULT 'default',
  source_sender_id TEXT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.8,
  importance TEXT NOT NULL DEFAULT 'medium',
  source_thread_id TEXT,
  source_message_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  supersedes_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Use FTS5 table `memory_items_fts`.

Run:

```bash
uv run pytest tests/memory/test_memory_store.py -v
```

Expected: FAIL because file does not exist.

**Step 2: Implement minimal store**

Implement methods:

- `add_item(...) -> str`
- `search(query: str, *, limit: int = 5, kind: str | None = None) -> list[dict]`
- `list_recent(limit: int = 20) -> list[dict]`
- `mark_obsolete(memory_id: str) -> bool`
- `replace_item(memory_id: str, ..., supersedes_id: str | None = None) -> bool`

**Step 3: Verify**

Run:

```bash
uv run pytest tests/memory/test_memory_store.py -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add agent/memory/memory_store.py agent/memory/__init__.py tests/memory/test_memory_store.py
git commit -m "feat: add auditable memory store"
```

## Task 5: Add Automatic Memory Extraction After Successful Turns

**Files:**
- Create: `agent/memory/memory_extractor.py`
- Modify: `agent/core/loop.py`
- Modify: `agent/app.py`
- Test: `tests/memory/test_memory_extractor.py`
- Test: `tests/core/test_loop.py`

**Step 1: Write extractor tests**

Start with deterministic rule-based extraction. Do not call a real model.

Rules:

- explicit remember phrases create `kind=profile` or `kind=fact`;
- user preferences create `kind=preference`;
- one-off small talk creates no memory;
- bookkeeping messages create no memory item because ledger owns them;
- low-confidence extraction returns no write.

Run:

```bash
uv run pytest tests/memory/test_memory_extractor.py -v
```

Expected: FAIL.

**Step 2: Implement extractor**

Create a conservative extractor:

```python
class MemoryExtractor:
    def extract(self, *, user_message: str, assistant_message: str, thread_id: str, message_id: str, sender_id: str | None) -> list[MemoryCandidate]:
        ...
```

Use narrow Chinese trigger patterns first:

- `记住`
- `以后`
- `我喜欢`
- `我不喜欢`
- `我的...是`
- `叫我...`

Avoid writing if message contains only greetings or failed model/provider errors.

Do not write group-chat speaker facts as global user profile unless the message is explicit, for example `记住我叫...` from that sender.

**Step 3: Wire into AgentCore**

After assistant final text is appended and persisted, run extractor and write candidates to `MemoryStore`. This should not block or fail the main reply.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/memory/test_memory_extractor.py tests/core/test_loop.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/memory/memory_extractor.py agent/core/loop.py agent/app.py tests/memory/test_memory_extractor.py tests/core/test_loop.py
git commit -m "feat: extract durable memories after turns"
```

## Task 6: Inject Relevant Memory Before Model Calls

**Files:**
- Modify: `agent/core/context.py`
- Modify: `agent/core/loop.py`
- Modify: `agent/app.py`
- Test: `tests/core/test_context.py`
- Test: `tests/core/test_loop.py`

**Step 1: Write context tests**

Assert:

- profile memories are always injected, capped by count;
- search results for the current user message are injected;
- obsolete memories are not injected;
- injected memory appears in a dedicated `[MEMORY ITEMS]` block, separate from `MEMORY.md`;
- sender/chat metadata appears in `[HUMAN CONTEXT]`;
- token budget is bounded.

Run:

```bash
uv run pytest tests/core/test_context.py tests/core/test_loop.py -v
```

Expected: FAIL.

**Step 2: Add retrieval in AgentCore**

Before `ContextAssembler.assemble`, fetch:

- top recent/profile memories;
- top search matches for `message.body`.

Pass `memory_items_text` into the assembler.

**Step 3: Update assembler**

Add optional block:

```text
[HUMAN CONTEXT]
Current sender: <sender id>
Chat type: <private/group/unknown>

[MEMORY ITEMS]
- preference: ...
- profile: ...
```

Keep this separate from `[MEMORY.md]`.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/core/test_context.py tests/core/test_loop.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/core/context.py agent/core/loop.py agent/app.py tests/core/test_context.py tests/core/test_loop.py
git commit -m "feat: inject relevant memories into context"
```

## Task 7: Consolidate Note Tools Onto The New Memory Store

**Files:**
- Modify: `agent/tools/builtin/note_tools.py`
- Modify: `agent/tools/registry.py`
- Modify: `agent/memory/note_store.py`
- Test: `tests/memory/test_note_store.py`
- Test: `tests/tools/test_builtin_tools.py`
- Test: `tests/tools/test_registry.py`

**Step 1: Write compatibility tests**

Assert existing `note_add`, `note_search`, `note_update`, and `note_list_recent` behavior still works, but writes through or mirrors into the new memory store.

Run:

```bash
uv run pytest tests/memory/test_note_store.py tests/tools/test_builtin_tools.py tests/tools/test_registry.py -v
```

Expected: FAIL until wiring exists.

**Step 2: Decide compatibility layer**

Recommended conservative approach:

- Keep `notes` table for backward compatibility.
- New automatic memory uses `memory_items`.
- Tool `note_add` writes to both `notes` and `memory_items`.
- Tool `note_search` searches `memory_items` first, then legacy notes.

**Step 3: Verify**

Run:

```bash
uv run pytest tests/memory/test_note_store.py tests/tools/test_builtin_tools.py tests/tools/test_registry.py -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add agent/tools/builtin/note_tools.py agent/tools/registry.py agent/memory/note_store.py tests/memory/test_note_store.py tests/tools/test_builtin_tools.py tests/tools/test_registry.py
git commit -m "refactor: unify notes with memory items"
```

## Task 8: Improve Provider Failure Handling For Feishu

**Files:**
- Modify: `agent/providers/openai_compat.py`
- Modify: `agent/core/provider_manager.py`
- Modify: `agent/gateway/server.py`
- Test: `tests/providers/test_openai_compat.py`
- Test: `tests/providers/test_provider_manager.py`
- Test: `tests/gateway/test_feishu_cards.py`

**Step 1: Write tests**

Assert provider 403 quota failures become a typed or classified error with a user-friendly message:

```text
模型额度或账号模式不可用，请检查 provider 配置或关闭 free-tier-only 限制。
```

The detailed upstream error should remain in logs, not be dumped raw into Feishu cards.

Run:

```bash
uv run pytest tests/providers/test_openai_compat.py tests/providers/test_provider_manager.py tests/gateway/test_feishu_cards.py -v
```

Expected: FAIL because raw exception is currently sent to the user.

**Step 2: Add provider error classification**

Create a small domain exception in `agent/core/provider.py` or provider manager:

```python
class ProviderQuotaError(RuntimeError):
    user_message = "模型额度或账号模式不可用，请检查 provider 配置。"
```

Map `AllocationQuota.FreeTierOnly`, HTTP 403, and quota-like errors.

**Step 3: Update Gateway error rendering**

Show concise error in Feishu card, keep raw error in logs and `channel_messages.error_message`.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/providers/test_openai_compat.py tests/providers/test_provider_manager.py tests/gateway/test_feishu_cards.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/core/provider.py agent/providers/openai_compat.py agent/core/provider_manager.py agent/gateway/server.py tests/providers/test_openai_compat.py tests/providers/test_provider_manager.py tests/gateway/test_feishu_cards.py
git commit -m "fix: classify provider quota failures"
```

## Task 9: Add Memory Inspection Commands For Debugging

**Files:**
- Modify: `agent/tools/builtin/memory_tools.py`
- Modify: `agent/tools/registry.py`
- Modify: `agent/web/app.py`
- Test: `tests/tools/test_builtin_tools.py`
- Test: `tests/web/test_runtime_state.py`

**Step 1: Write tests**

Add tool tests for:

- `memory_search`
- `memory_list_recent`
- `memory_mark_obsolete`

Run:

```bash
uv run pytest tests/tools/test_builtin_tools.py tests/web/test_runtime_state.py -v
```

Expected: FAIL.

**Step 2: Implement tools**

Expose memory tools separately from `memory_write`:

- `memory_search(query, limit)`
- `memory_list_recent(limit)`
- `memory_forget(memory_id)`

Do not expose arbitrary `MEMORY.md` replacement as the primary path.

**Step 3: Optional Web route**

If current Web app already has runtime inspection patterns, add read-only `/api/memories` endpoint. Keep write/delete for later if it would expand scope too much.

**Step 4: Verify**

Run:

```bash
uv run pytest tests/tools/test_builtin_tools.py tests/web/test_runtime_state.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/tools/builtin/memory_tools.py agent/tools/registry.py agent/web/app.py tests/tools/test_builtin_tools.py tests/web/test_runtime_state.py
git commit -m "feat: add memory inspection tools"
```

## Task 10: End-To-End Verification And Docs

**Files:**
- Modify: `README.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `docs/KNOWN_ISSUES.md`
- Modify: `VERIFICATION_CHECKLIST.md`
- Test: existing full suite

**Step 1: Update docs**

Document:

- single subject default;
- how to configure one Feishu bot;
- how memory extraction works;
- how to inspect memory;
- how to enable M-flow experimentally;
- how provider quota errors should be fixed operationally.

**Step 2: Run targeted suite**

```bash
uv run pytest tests/config tests/gateway tests/core tests/memory tests/tools tests/providers -v
```

Expected: PASS.

**Step 3: Run full suite**

```bash
uv run pytest -v
```

Expected: PASS.

**Step 4: Manual boot smoke test**

Testing mode:

```bash
uv run python -m agent.main --mode cli --testing
```

Gateway config load:

```bash
uv run python -m agent.main --mode gateway --testing --config config/agent.linux.yaml
```

Expected:

- one workspace;
- no M-flow startup warning by default;
- no multi-runtime Heartbeat/Cron warning for single instance;
- memory store tables created in `workspace/agent.db`.

**Step 5: Commit**

```bash
git add README.md docs/TROUBLESHOOTING.md docs/KNOWN_ISSUES.md VERIFICATION_CHECKLIST.md
git commit -m "docs: describe single subject memory runtime"
```

## Rollout Plan

1. Backup existing runtime directories:

```bash
cp -a workspace workspace.backup.$(date +%Y%m%d%H%M%S) || true
cp -a workspace-main workspace-main.backup.$(date +%Y%m%d%H%M%S) || true
cp -a workspace-ops workspace-ops.backup.$(date +%Y%m%d%H%M%S) || true
```

2. Choose source memory:

- Use `workspace-main/MEMORY.md` as initial `workspace/MEMORY.md`.
- Optionally migrate `workspace-main/agent.db` notes into `workspace/agent.db`.
- Do not migrate `workspace-ops` unless there is real data.

3. Deploy:

```bash
git pull
uv sync
yimin restart
```

4. Validate logs:

```bash
yimin logs
```

Look for:

- one Feishu adapter connected;
- no M-flow warnings;
- provider request config logs;
- memory extraction logs after successful turns.

## Risk Notes

- Provider quota errors are operational unless the key/provider is changed. Code can make the error clear, but cannot fix an exhausted free-tier account.
- Automatic memory extraction must start conservative. False negatives are cheaper than embarrassing false positives.
- Do not delete legacy `notes` or `sessions` data in this refactor.
- Keep multi-runtime code available but advanced; ripping it out would create unnecessary churn.
