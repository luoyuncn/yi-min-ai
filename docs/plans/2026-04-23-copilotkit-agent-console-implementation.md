# CopilotKit Agent Console Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade the first AG-UI web slice into a CopilotKit-based agent console with interrupt controls, approval suspension/resume, and complete thread restore / multi-thread switching.

**Architecture:** Keep the Python agent runtime as the source of truth. Extend the backend with replayable thread APIs, cooperative run interruption, and pending approval storage. Replace the static HTML console with a React + CopilotKit frontend using a custom AG-UI `HttpAgent` subclass instead of the CopilotKit Node runtime.

**Tech Stack:** Python 3.12, FastAPI, `ag-ui-protocol`, SQLite, pytest, React, TypeScript, Vite, `@copilotkit/react-core`, `@ag-ui/client`

---

### Task 1: Make session archive replayable

**Files:**
- Modify: `agent/memory/session_archive.py`
- Modify: `agent/session/manager.py`
- Test: `tests/memory/test_session_archive.py`
- Test: `tests/session/test_manager.py`

**Step 1: Write the failing tests**

- Add a session-archive test that persists internal messages with IDs / tool metadata and loads them back in order.
- Add a session-manager test that recreates a session from SQLite after the in-memory cache is empty.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/memory/test_session_archive.py tests/session/test_manager.py -v`
Expected: FAIL because archive replay and session restore do not exist yet.

**Step 3: Write minimal implementation**

- Extend the archive schema to store:
  - plain text content for FTS
  - full message payload JSON
  - recorded timestamp
- Add:
  - `load_session(session_id)`
  - `list_sessions(limit=...)`
  - thread summary helpers
- Update `SessionManager.get_or_create()` to restore from archive when possible.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_session_archive.py tests/session/test_manager.py -v`
Expected: PASS

---

### Task 2: Add runtime control and approval suspension primitives

**Files:**
- Modify: `agent/web/events.py`
- Modify: `agent/core/loop.py`
- Create: `agent/web/runtime_state.py`
- Test: `tests/core/test_loop_events.py`
- Test: `tests/web/test_runtime_state.py`

**Step 1: Write the failing tests**

- Add a runtime-state test for:
  - registering / resolving an active run control
  - storing / loading a pending approval
- Add core-loop tests for:
  - approval interruption emitting a custom interrupt event
  - resume execution after approval
  - cooperative run interruption

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_loop_events.py tests/web/test_runtime_state.py -v`
Expected: FAIL because approval and interrupt runtime state does not exist yet.

**Step 3: Write minimal implementation**

- Add runtime events for:
  - `MessagesSnapshotEvent`
  - `CustomEvent`
- Add a web runtime state module containing:
  - active run controller registry
  - pending approval store
- Update `AgentCore.run_events()` to:
  - accept runtime control / approval dependencies
  - gate selected tools behind approval
  - emit `CustomEvent(name="on_interrupt")`
  - resume from `forwardedProps.command.resume`
  - stop cleanly when interrupted

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_loop_events.py tests/web/test_runtime_state.py -v`
Expected: PASS

---

### Task 3: Add complete thread and control APIs

**Files:**
- Modify: `agent/app.py`
- Modify: `agent/web/ag_ui_adapter.py`
- Modify: `agent/web/app.py`
- Test: `tests/integration/test_web_app.py`
- Test: `tests/web/test_ag_ui_adapter.py`

**Step 1: Write the failing tests**

- Add adapter tests for:
  - `MessagesSnapshotEvent -> MESSAGES_SNAPSHOT`
  - `CustomEvent -> CUSTOM`
- Add integration tests for:
  - listing threads
  - connecting to a thread and receiving history replay
  - interrupting an active run
  - resuming a pending approval

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_ag_ui_adapter.py tests/integration/test_web_app.py -v`
Expected: FAIL because the new endpoints and event mappings are missing.

**Step 3: Write minimal implementation**

- Upgrade `AgentApplication.stream_events()` so web callers can pass runtime-control context.
- Extend the adapter for snapshot and custom events.
- Add endpoints:
  - `GET /api/threads`
  - `GET /api/threads/{thread_id}`
  - `POST /api/threads/{thread_id}/connect`
  - `POST /api/threads/{thread_id}/runs/{run_id}/interrupt`
- Make `/api/threads/{thread_id}/runs` accept both legacy payloads and AG-UI `RunAgentInput`.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_ag_ui_adapter.py tests/integration/test_web_app.py -v`
Expected: PASS

---

### Task 4: Scaffold the CopilotKit frontend

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app.tsx`
- Create: `frontend/src/styles.css`

**Step 1: Write the failing checks**

- Add the frontend workspace and build scripts before implementation.
- Run the build once and confirm it fails because the app code does not exist yet.

**Step 2: Run build to verify failure**

Run: `npm --prefix frontend run build`
Expected: FAIL because the initial frontend source is incomplete.

**Step 3: Write minimal implementation**

- Install and wire:
  - `react`
  - `react-dom`
  - `typescript`
  - `vite`
  - `@copilotkit/react-core`
  - `@ag-ui/client`
- Configure Vite output into `agent/web/static/app`.
- Render a minimal CopilotKit app shell that mounts successfully.

**Step 4: Run build to verify it passes**

Run: `npm --prefix frontend run build`
Expected: PASS

---

### Task 5: Implement CopilotKit chat, thread switching, and approval UI

**Files:**
- Create: `frontend/src/lib/yi-min-http-agent.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/thread-sidebar.tsx`
- Create: `frontend/src/components/approval-card.tsx`
- Modify: `frontend/src/app.tsx`
- Modify: `frontend/src/styles.css`

**Step 1: Add the failing behavior check**

- Run the frontend build and inspect the app shell expectations:
  - custom agent class missing
  - thread sidebar missing
  - approval UI missing

**Step 2: Implement the feature**

- Add `YiMinHttpAgent`:
  - run against `/api/threads/{thread_id}/runs`
  - connect against `/api/threads/{thread_id}/connect`
- Add a thread list hook using the JSON thread endpoints.
- Add new-thread creation and selected-thread switching.
- Add `useInterrupt({ renderInChat: false, ... })` approval UI.
- Add explicit stop handling through the interrupt endpoint.
- Keep the layout mobile-safe and desktop-friendly.

**Step 3: Rebuild**

Run: `npm --prefix frontend run build`
Expected: PASS

---

### Task 6: Serve the built frontend and refresh docs

**Files:**
- Modify: `agent/web/app.py`
- Modify: `README.md`

**Step 1: Write the failing expectation**

- Confirm the backend still serves the old static shell or does not serve the built assets correctly.

**Step 2: Implement**

- Serve the built Vite output from FastAPI.
- Keep a safe fallback if the build output is absent.
- Update README with:
  - frontend build command
  - web startup flow
  - thread / approval / interrupt notes

**Step 3: Verify**

Run:
- `npm --prefix frontend run build`
- `uv run pytest -q`
- `uv run python -m agent.web.main --config config/agent.yaml --testing --port 8011`

Expected:
- frontend build succeeds
- Python tests remain green
- web server starts and serves the CopilotKit app

---

### Task 7: End-to-end smoke verification

**Files:**
- No new code required unless verification exposes defects

**Step 1: Verify thread replay**

Run the web app, create two different threads, then reload and switch back to the first thread.  
Expected: transcript is restored from SQLite.

**Step 2: Verify approval**

Trigger a gated tool call.  
Expected:
- approval card appears
- approve / reject both resume the run
- refreshing the page before acting replays the pending approval when reconnecting to the same thread

**Step 3: Verify interrupt**

Start a long-ish test run and click stop.  
Expected: backend ends the run cleanly and the UI remains usable.
