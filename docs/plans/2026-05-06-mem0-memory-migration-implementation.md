# Mem0 Memory Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current long-term memory path with Mem0 while keeping ledger and notes as separate applications, and turn `SOUL.md` and `PROFILE.md` into controlled writable projections instead of human-maintained source files.

**Architecture:** Keep `SOUL.md` and `PROFILE.md` as always-loaded local context files, but stop treating them as manually maintained truth sources. Introduce controlled identity/profile services that own structured writes and render those files as projections. Move all durable conversational memory, recall, correction, and retrieval to a new Mem0-backed memory service that sits between `AgentCore` and the model context. Ledger and notes remain independent stores and are no longer treated as part of the memory subsystem.

**Tech Stack:** Python 3.12, existing AgentCore/Gateway runtime, Mem0 OSS or self-hosted REST API, pytest, existing OpenAI-compatible provider layer, SQLite for ledger/notes/session archive.

---

## Product Decision

- `ledger` is a standalone finance app.
- `notes` is a standalone note app.
- `memory` is its own subsystem and Mem0 is the source of truth for long-term memory.
- `SOUL.md` remains the assistant identity authority at runtime, but is produced by a controlled write path instead of manual editing.
- `PROFILE.md` remains core user profile context at runtime, but is produced by a controlled write path instead of manual editing.
- Session archive remains for replay, debugging, and evidence, not as the primary long-term memory store.

## Non-Goals

- Do not migrate ledger entries into Mem0.
- Do not migrate note bodies into Mem0.
- Do not keep the current `memory_items` SQLite table as the long-term truth source.
- Do not rely on hardcoded keyword routing for memory questions.
- Do not let the model claim a memory was saved unless Mem0 write succeeded.
- Do not let arbitrary model output directly overwrite `SOUL.md` or `PROFILE.md`.

## Target Runtime Model

- `SOUL.md`: assistant identity, name, style, persona. Rendered from a controlled identity store/service.
- `PROFILE.md`: core user facts that should always load, even if Mem0 is unavailable. Rendered from a controlled profile store/service.
- `Mem0`: durable user memory store for preferences, profile facts, stable plans, constraints, and relationships.
- `SessionArchive`: replay and audit evidence.
- `NoteStore`: user-operated notebook only.
- `LedgerStore`: user-operated bookkeeping only.

## Entity Mapping

Use Mem0 entity scoping like this:

- `user_id`: current sender id, for example Feishu `open_id`
- `agent_id`: stable assistant identity, for example `"yi-min"`
- `run_id`: runtime thread key, for example `feishu:feishu:<chat_id>`

Rules:

- User memory should usually be stored under `user_id`.
- Assistant persona memory, if needed, should be stored under `agent_id`.
- Temporary thread-local facts should use `run_id`.
- If Mem0 is unavailable, the system must degrade to `SOUL.md + PROFILE.md + session history` without pretending writes succeeded.
- Assistant identity updates must not go to Mem0 first; they go through the identity service and then render `SOUL.md`.
- Core profile updates must not go to Mem0 first; they go through the profile service and then render `PROFILE.md`.

## Acceptance Criteria

1. Asking identity or preference questions uses Mem0-backed retrieval, not the old local memory store.
2. Successful long-term memory saves are auditable through Mem0 results or request logs.
3. Notes and ledger no longer participate in automatic memory injection.
4. The current `memory_items` SQLite path is removed from the hot path.
5. If Mem0 write/search fails, the user sees a truthful fallback response instead of a fake “已记住”.
6. `SOUL.md` still overrides conflicting assistant identity data.
7. `PROFILE.md` still loads every turn, but is not silently rewritten by memory extraction.
8. Assistant identity and core profile can be customized through controlled tools or service methods without manual file editing.

## Task 1: Add Mem0 Configuration And Dependency Boundary

**Files:**
- Modify: `pyproject.toml`
- Modify: `config/agent.yaml`
- Modify: `config/agent.linux.yaml`
- Modify: `agent/config/models.py`
- Modify: `agent/config/loader.py`
- Test: `tests/config/test_loader.py`

**Step 1: Write the failing config tests**

Add tests that assert:

- settings can represent a Mem0 section;
- Mem0 can be enabled independently of M-flow;
- default config keeps Mem0 disabled until explicitly configured;
- config supports `base_url`, `api_key_env`, `org_id`, `project_id`, `agent_id`, and `enabled`.

Example:

```python
def test_loader_parses_mem0_settings(tmp_path):
    config = tmp_path / "agent.yaml"
    config.write_text(
        "agent:\n"
        "  name: Yi Min\n"
        "  workspace_dir: ./workspace\n"
        "mem0:\n"
        "  enabled: true\n"
        "  base_url: http://localhost:8888\n"
        "  api_key_env: MEM0_API_KEY\n"
        "  agent_id: yi-min\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.mem0.enabled is True
    assert settings.mem0.agent_id == "yi-min"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_loader.py -v`

Expected: FAIL because `mem0` settings do not exist yet.

**Step 3: Write minimal implementation**

Add a typed Mem0 settings model:

```python
class Mem0Settings(BaseModel):
    enabled: bool = False
    base_url: str | None = None
    api_key_env: str = "MEM0_API_KEY"
    org_id: str | None = None
    project_id: str | None = None
    agent_id: str = "yi-min"
```

Wire it into the top-level settings and loader defaults.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/config/test_loader.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add pyproject.toml config/agent.yaml config/agent.linux.yaml agent/config/models.py agent/config/loader.py tests/config/test_loader.py
git commit -m "feat: add mem0 runtime configuration"
```

## Task 2: Introduce Controlled SOUL And PROFILE Write Services

**Files:**
- Create: `agent/memory/identity_store.py`
- Create: `agent/memory/profile_store.py`
- Modify: `agent/memory/always_on.py`
- Modify: `agent/memory/__init__.py`
- Test: `tests/memory/test_identity_store.py`
- Test: `tests/memory/test_always_on.py`

**Step 1: Write the failing service tests**

Cover:

- update assistant identity through structured fields;
- render `SOUL.md` from structured assistant identity data;
- update core user profile through structured fields;
- render `PROFILE.md` from structured user profile data;
- forbid direct arbitrary overwrite from model text blobs.

Example:

```python
def test_identity_store_renders_soul_file(tmp_path):
    store = IdentityStore(tmp_path / "agent.db", tmp_path / "SOUL.md")
    store.replace_identity(name="银月", style="冷静", principles=["不编造"])
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "你是银月" in text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_identity_store.py tests/memory/test_always_on.py -v`

Expected: FAIL because the stores do not exist yet.

**Step 3: Write minimal implementation**

Create two controlled services:

```python
class IdentityStore:
    def replace_identity(self, *, name: str, backstory: str, style: str, principles: list[str]) -> None: ...

class ProfileStore:
    def replace_profile(self, *, display_name: str | None, core_facts: list[str]) -> None: ...
```

Each service writes structured state first, then renders the corresponding markdown file.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/test_identity_store.py tests/memory/test_always_on.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/memory/identity_store.py agent/memory/profile_store.py agent/memory/always_on.py agent/memory/__init__.py tests/memory/test_identity_store.py tests/memory/test_always_on.py
git commit -m "feat: add controlled soul and profile stores"
```

## Task 3: Introduce A Mem0 Memory Service

**Files:**
- Create: `agent/memory/mem0_service.py`
- Modify: `agent/memory/__init__.py`
- Test: `tests/memory/test_mem0_service.py`

**Step 1: Write the failing service tests**

Cover:

- initialize disabled service;
- initialize enabled service with fake client;
- add memories for a user;
- search memories by `user_id` and `run_id`;
- map sender/thread/agent fields correctly;
- return structured failure instead of raising raw exceptions.

Example:

```python
def test_mem0_service_searches_by_sender_scope():
    client = FakeMem0Client(results=[{"id": "m1", "memory": "用户喜欢冷萃"}])
    service = Mem0MemoryService(client=client, agent_id="yi-min", enabled=True)
    rows = service.search(query="我喜欢喝什么", user_id="ou_123", run_id="feishu:feishu:chat-1")
    assert rows[0]["memory"] == "用户喜欢冷萃"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_mem0_service.py -v`

Expected: FAIL because file does not exist.

**Step 3: Write minimal implementation**

Create a thin wrapper:

```python
class Mem0MemoryService:
    def __init__(self, *, enabled: bool, agent_id: str, client=None): ...
    def add(self, messages: list[dict], *, user_id: str, run_id: str) -> dict: ...
    def search(self, query: str, *, user_id: str, run_id: str, top_k: int = 5) -> list[dict]: ...
    def get_all(self, *, user_id: str) -> list[dict]: ...
```

Keep one place where Mem0 SDK or REST client details live.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/test_mem0_service.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/memory/mem0_service.py agent/memory/__init__.py tests/memory/test_mem0_service.py
git commit -m "feat: add mem0 memory service"
```

## Task 4: Add Controlled Tools For Assistant Identity And Core Profile

**Files:**
- Create: `agent/tools/builtin/identity_tools.py`
- Modify: `agent/tools/registry.py`
- Modify: `agent/app.py`
- Test: `tests/tools/test_builtin_tools.py`
- Test: `tests/tools/test_registry.py`

**Step 1: Write the failing tool tests**

Assert:

- assistant identity can be updated through a dedicated tool;
- core profile can be updated through a dedicated tool;
- tool success rewrites rendered `SOUL.md` and `PROFILE.md`;
- free-form memory text cannot directly overwrite these files.

Example:

```python
def test_assistant_identity_update_renders_soul_file(tmp_path):
    store = IdentityStore(tmp_path / "agent.db", tmp_path / "SOUL.md")
    result = assistant_identity_update(store, name="银月", style="冷静", principles=["不编造"])
    assert result == "ok"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/test_builtin_tools.py tests/tools/test_registry.py -v`

Expected: FAIL because these tools do not exist yet.

**Step 3: Write minimal implementation**

Add tools such as:

```python
assistant_identity_update(...)
profile_core_update(...)
```

Expose them in the tool registry and inject the new stores through app assembly.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/test_builtin_tools.py tests/tools/test_registry.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/tools/builtin/identity_tools.py agent/tools/registry.py agent/app.py tests/tools/test_builtin_tools.py tests/tools/test_registry.py
git commit -m "feat: add controlled soul and profile update tools"
```

## Task 5: Replace Local Durable Memory Injection With Mem0 Retrieval

**Files:**
- Modify: `agent/core/loop.py`
- Modify: `agent/core/context.py`
- Modify: `agent/app.py`
- Test: `tests/core/test_context.py`
- Test: `tests/core/test_loop.py`

**Step 1: Write the failing context tests**

Assert:

- context contains a Mem0-derived memory block when search returns results;
- context no longer depends on local `memory_items`;
- `PROFILE.md` still appears separately;
- `SOUL.md` still appears separately;
- if Mem0 is unavailable, the memory block says none found instead of crashing.

Example:

```python
def test_context_assembler_includes_mem0_memory_block():
    context = assembler.assemble(
        soul_text="# SOUL",
        memory_text="# User Profile",
        memory_items_text="- preference: 用户喜欢冷萃",
        tool_index="可用工具：",
        skill_index="可用技能：",
        history=[],
        user_message="我喜欢喝什么？",
    )
    assert "[检索到的长期记忆]" in context[0]["content"]
    assert "用户喜欢冷萃" in context[0]["content"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_context.py tests/core/test_loop.py -v`

Expected: FAIL because retrieval still uses the local memory path.

**Step 3: Write minimal implementation**

In `AgentCore`, replace `_build_memory_items_text()` to call the new Mem0 service. Keep the contract:

```python
memory_items_text = self.mem0_memory_service.build_context_block(
    query=message.body,
    user_id=message.sender,
    run_id=thread_id,
)
```

Remove `memory_store.search(...)` from the hot path.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_context.py tests/core/test_loop.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/core/loop.py agent/core/context.py agent/app.py tests/core/test_context.py tests/core/test_loop.py
git commit -m "refactor: inject mem0 retrieval into model context"
```

## Task 6: Replace Local Automatic Extraction With Mem0 Writes

**Files:**
- Modify: `agent/core/loop.py`
- Modify: `agent/memory/memory_extractor.py`
- Modify: `agent/app.py`
- Test: `tests/memory/test_memory_extractor.py`
- Test: `tests/core/test_loop.py`

**Step 1: Write the failing write-path tests**

Assert:

- after a successful assistant reply, long-term memory extraction sends the turn to Mem0;
- the system does not say “已记住” unless Mem0 add succeeds;
- provider or Mem0 failure does not crash the turn;
- explicit remember requests still produce a confirmation only after save success.

Example:

```python
def test_successful_turn_writes_memory_to_mem0():
    mem0 = FakeMem0Service(add_result={"results": [{"id": "m1"}]})
    core = build_core(mem0_service=mem0)
    result = core.run_sync(message)
    assert mem0.add_calls
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_memory_extractor.py tests/core/test_loop.py -v`

Expected: FAIL because current extraction writes only to local store.

**Step 3: Write minimal implementation**

Keep the extractor conservative, but hand off persistence to Mem0:

```python
payload = self.memory_extractor.build_mem0_messages(
    user_message=user_message,
    assistant_message=assistant_text,
)
self.mem0_memory_service.add(payload, user_id=sender_id, run_id=thread_id)
```

If save fails:

- log it;
- do not claim success;
- return normal assistant text unless the assistant explicitly promised a save.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/test_memory_extractor.py tests/core/test_loop.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/core/loop.py agent/memory/memory_extractor.py agent/app.py tests/memory/test_memory_extractor.py tests/core/test_loop.py
git commit -m "refactor: persist long-term memory through mem0"
```

## Task 7: Remove Old Local Memory Store From The Primary Runtime

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/tools/registry.py`
- Modify: `agent/tools/builtin/memory_tools.py`
- Modify: `agent/memory/__init__.py`
- Test: `tests/tools/test_registry.py`
- Test: `tests/tools/test_builtin_tools.py`

**Step 1: Write the failing registry tests**

Assert:

- primary runtime no longer requires `MemoryStore`;
- memory inspection tools call Mem0 service instead of local SQLite memory;
- `profile_write` still exists if you choose to keep manual profile editing;
- old `memory_search` and `memory_list_recent` are backed by Mem0 or hidden entirely.

Example:

```python
def test_registry_uses_mem0_backed_memory_tools(tmp_path):
    registry = build_stage1_registry(
        workspace_dir=tmp_path,
        always_on_memory=None,
        session_archive=None,
        skill_loader=None,
        mem0_memory_service=FakeMem0Service(enabled=True),
    )
    assert "memory_search" in registry.names()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/test_registry.py tests/tools/test_builtin_tools.py -v`

Expected: FAIL because registry still depends on local memory store.

**Step 3: Write minimal implementation**

Change runtime assembly:

- stop constructing `MemoryStore` for the primary memory path;
- pass `mem0_memory_service` into the registry;
- reimplement `memory_search` and `memory_list_recent` as Mem0-backed debug tools.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/test_registry.py tests/tools/test_builtin_tools.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/app.py agent/tools/registry.py agent/tools/builtin/memory_tools.py agent/memory/__init__.py tests/tools/test_registry.py tests/tools/test_builtin_tools.py
git commit -m "refactor: remove local memory store from primary runtime"
```

## Task 8: Make Notes And Ledger Explicitly Independent From Memory

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/core/loop.py`
- Modify: `agent/tools/builtin/note_tools.py`
- Modify: `agent/tools/builtin/ledger_tools.py`
- Test: `tests/core/test_loop.py`
- Test: `tests/tools/test_builtin_tools.py`

**Step 1: Write the failing behavior tests**

Assert:

- note saves do not create Mem0 memories;
- ledger writes do not create Mem0 memories;
- automatic memory extraction ignores bookkeeping and notebook tool results;
- memory retrieval does not read note bodies or ledger entries.

Example:

```python
def test_note_add_does_not_write_to_mem0():
    mem0 = FakeMem0Service()
    result = note_add(note_store, note_type="plan", title="t", content="c", importance="medium", is_user_explicit=True)
    assert mem0.add_calls == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_loop.py tests/tools/test_builtin_tools.py -v`

Expected: FAIL until boundaries are explicit.

**Step 3: Write minimal implementation**

Add explicit exclusions:

```python
if tool_name in {"ledger_upsert_draft", "ledger_commit_draft", "note_add", "note_update"}:
    skip_memory_write = True
```

Also keep extractor rules conservative so raw note or ledger content is not promoted into memory.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_loop.py tests/tools/test_builtin_tools.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/app.py agent/core/loop.py agent/tools/builtin/note_tools.py agent/tools/builtin/ledger_tools.py tests/core/test_loop.py tests/tools/test_builtin_tools.py
git commit -m "refactor: separate notes and ledger from memory subsystem"
```

## Task 9: Add Fallback, Audit, And Observability For Mem0

**Files:**
- Modify: `agent/memory/mem0_service.py`
- Modify: `agent/observability/react_log.py`
- Modify: `agent/gateway/server.py`
- Modify: `docs/TROUBLESHOOTING.md`
- Test: `tests/memory/test_mem0_service.py`
- Test: `tests/gateway/test_server.py`

**Step 1: Write the failing observability tests**

Assert:

- Mem0 failures are logged with `user_id`, `run_id`, and operation name;
- the user sees a truthful degraded message when a save was explicitly requested and Mem0 failed;
- search failure does not fabricate memory results.

Example:

```python
def test_mem0_failure_returns_truthful_result():
    service = Mem0MemoryService(client=FailingClient(), enabled=True, agent_id="yi-min")
    result = service.search("query", user_id="u1", run_id="r1")
    assert result == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/test_mem0_service.py tests/gateway/test_server.py -v`

Expected: FAIL until fallback behavior is explicit.

**Step 3: Write minimal implementation**

Add structured outcomes:

```python
{"ok": False, "error": "mem0 unavailable", "results": []}
```

Log every Mem0 add/search failure with enough identifiers to debug cross-user contamination or write loss.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/test_mem0_service.py tests/gateway/test_server.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/memory/mem0_service.py agent/observability/react_log.py agent/gateway/server.py docs/TROUBLESHOOTING.md tests/memory/test_mem0_service.py tests/gateway/test_server.py
git commit -m "feat: add mem0 fallback and observability"
```

## Task 10: Remove Or Freeze Legacy Memory Paths

**Files:**
- Modify: `agent/memory/memory_store.py`
- Modify: `agent/memory/mflow_bridge.py`
- Modify: `agent/app.py`
- Modify: `README.md`
- Modify: `docs/KNOWN_ISSUES.md`
- Test: `tests/test_app.py`

**Step 1: Write the failing migration tests**

Assert:

- app boot does not require local memory store for long-term memory;
- M-flow remains optional and off the hot path;
- docs clearly say Mem0 owns long-term memory.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v`

Expected: FAIL until docs and runtime are aligned.

**Step 3: Write minimal implementation**

Choose one:

- keep `memory_store.py` as deprecated compatibility code with warnings;
- or remove it from exports and tests that still imply it is the primary path.

Keep `mflow_bridge.py` experimental and clearly marked non-default.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add agent/memory/memory_store.py agent/memory/mflow_bridge.py agent/app.py README.md docs/KNOWN_ISSUES.md tests/test_app.py
git commit -m "docs: mark mem0 as the primary long-term memory path"
```

## Task 11: End-To-End Verification And Rollout

**Files:**
- Modify: `VERIFICATION_CHECKLIST.md`
- Modify: `README.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Test: existing suite

**Step 1: Update verification docs**

Document:

- Mem0 boot requirements;
- required environment variables;
- how to inspect memories in Mem0 dashboard or API;
- how to verify notes and ledger remain separate;
- how to simulate Mem0 outage.

**Step 2: Run targeted suite**

Run:

```bash
uv run pytest tests/config tests/core tests/memory tests/tools tests/gateway -v
```

Expected: PASS.

**Step 3: Run full suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS.

**Step 4: Manual smoke test**

Run:

```bash
uv run python -m agent.main --mode gateway --config config/agent.yaml
```

Verify:

- Mem0 connects or degrades honestly;
- `SOUL.md` still controls assistant identity;
- explicit “记住我喜欢冷萃” produces a real Mem0 record;
- “我的笔记有哪些” still uses notes only;
- “我总共有几笔账目” still uses ledger only.

**Step 5: Commit**

```bash
git add VERIFICATION_CHECKLIST.md README.md docs/TROUBLESHOOTING.md
git commit -m "docs: verify mem0-centered memory architecture"
```

## Rollout Notes

1. Back up the current workspace before migration.
2. Migrate current assistant identity into the new identity store, then render `SOUL.md`.
3. Migrate current core user profile into the new profile store, then render `PROFILE.md`.
4. Do not bulk import notes or ledger into Mem0.
5. If you want migration from existing local memory later, write a separate one-off importer from `memory_items` into Mem0 and run it once.

## References

- Mem0 introduction and positioning: `https://docs.mem0.ai/introduction`
- Mem0 self-hosted setup and audit log: `https://docs.mem0.ai/open-source/setup`
- Mem0 entity scoping: `https://docs.mem0.ai/platform/features/entity-scoped-memory`
- Mem0 custom instructions: `https://docs.mem0.ai/open-source/features/custom-instructions`
- Mem0 REST API overview: `https://docs.mem0.ai/api-reference`
