# CopilotKit Agent Console Design

**Date:** 2026-04-23  
**Status:** Approved for implementation  
**Decision:** Keep the Python runtime and AG-UI SSE backend, replace the static browser shell with a CopilotKit-based React console, and add backend-backed interrupt / approval / thread replay primitives.

---

## Context

The repository already has a first Web slice:

- FastAPI entrypoint
- AG-UI SSE event adapter
- runtime event streaming from `AgentCore`
- a plain static HTML console

That first slice validated the backend direction, but it is still missing three pieces that make the Web UI feel like a real agent console instead of a protocol demo:

1. a production-grade agent chat surface
2. human-in-the-loop approval and explicit stop/interrupt controls
3. durable thread restore and multi-thread switching

The user already chose the architecture direction: preserve the Python runtime and do not migrate the runtime to LangGraph.

---

## External Integration Choice

The frontend will use **CopilotKit v2 on top of `@ag-ui/client`**, not the CopilotKit Node runtime.

That choice is deliberate:

- CopilotKit v2 already exposes AG-UI-native React components and hooks such as `CopilotChat`, `useAgent`, and `useInterrupt`.
- `useAgent` can work with self-managed agents via `agents__unsafe_dev_only`, so the frontend does not need CopilotKit's own backend runtime contract.
- `useInterrupt` currently reacts to a `CustomEvent` named `on_interrupt`, which maps cleanly onto our Python backend without requiring a LangGraph migration.

This gives us a modern agent UI while keeping the existing Python backend as the source of truth.

---

## Goals

Build a local-first CopilotKit agent console that supports:

- chat with the current Python agent runtime
- visible tool calls and streaming assistant output
- interrupting an active run from the Web UI
- human approval for selected tools
- restoring existing threads from SQLite-backed history
- switching between multiple threads in the UI
- replaying pending approvals after refresh or thread switch

Non-goals for this phase:

- multi-user auth
- cloud sync
- binary attachments
- durable pending approvals across process restarts
- migrating backend orchestration to JS or LangGraph

---

## High-Level Architecture

```text
CopilotKit React Console
  -> custom YiMinHttpAgent (@ag-ui/client)
    -> FastAPI AG-UI endpoints
      -> AgentApplication / AgentCore
        -> SessionManager
        -> SessionArchive (SQLite)
        -> ProviderManager / ToolExecutor
        -> Web runtime control + approval stores
```

Three additions are required beyond the first slice:

1. **Replayable thread history**
   The backend must list archived threads, rebuild a thread's message history from SQLite, and expose a connect/replay stream for a selected thread.

2. **Runtime control**
   Active runs need a cooperative interrupt mechanism so the Web UI can stop a run without killing the whole process.

3. **Approval suspension / resume**
   Certain tools must be able to pause a run, surface an approval UI, and later resume execution from the pending tool call.

---

## Frontend Design

### CopilotKit integration model

The frontend will use:

- `CopilotKitProvider` with `agents__unsafe_dev_only`
- a custom `YiMinHttpAgent` subclass built on `HttpAgent`
- `CopilotChat` as the primary transcript surface
- `useInterrupt` to render approval cards

This avoids depending on CopilotKit's `/info` / platform runtime APIs while still using the CopilotKit chat and agent experience.

### Thread UX

The console layout becomes:

- left rail: thread list, new thread action, thread metadata
- main pane: CopilotKit chat
- right rail or inline card: pending approval / run status / active thread metadata

Thread switching works by changing the selected `threadId` on the AG-UI agent. When the selected thread changes, the custom agent calls a backend connect endpoint and receives a `MESSAGES_SNAPSHOT` replay.

### Interrupt UX

The chat surface gets an explicit stop control. Stopping a run should:

- call a backend interrupt endpoint for the active `runId`
- let the backend end the stream cleanly
- keep the transcript intact

### Approval UX

When a gated tool is about to run, the backend emits:

- the tool start / args events
- a `CustomEvent(name="on_interrupt", value=...)`
- stream completion

The frontend uses `useInterrupt` to render Approve / Reject controls. On resolve, CopilotKit sends a new run with `forwardedProps.command.resume`, and the backend resumes from the pending approval.

---

## Backend API Design

### Existing endpoint kept

- `POST /api/threads/{thread_id}/runs`

This endpoint will be upgraded to accept both:

- the legacy `{ "text": "..." }` payload from the first static console
- AG-UI `RunAgentInput` payloads from `HttpAgent`

### New endpoints

- `GET /api/threads`
  Returns thread summaries derived from SQLite plus pending-approval metadata.

- `GET /api/threads/{thread_id}`
  Returns a single thread summary and message preview in JSON form.

- `POST /api/threads/{thread_id}/connect`
  Streams a `MESSAGES_SNAPSHOT` replay for the selected thread and re-emits a pending interrupt event if the thread is waiting on approval.

- `POST /api/threads/{thread_id}/runs/{run_id}/interrupt`
  Requests cooperative interruption for an active run.

---

## Persistence Model

### Session archive

`SessionArchive` must move from "search-only turn text" to "replayable message history":

- persist the full internal message payload as JSON
- keep searchable plain text in the current FTS-backed table
- store per-message timestamps so threads can be sorted by recency

This lets us support:

- full message replay
- thread list summaries
- restoring a thread into `SessionManager` after process-local eviction

### Pending approvals

Pending approvals will be held in an in-memory store keyed by approval ID and thread ID.

That store must capture:

- thread ID
- run ID
- the interrupted tool call
- the current loop context
- enough metadata to render a meaningful approval card after refresh

This phase does **not** persist pending approvals to SQLite. If the process restarts, pending approvals are dropped.

---

## Runtime Flow

### Normal run

1. Frontend sends a run through `YiMinHttpAgent`.
2. Backend converts it into a normalized input.
3. `AgentCore` runs as before and emits AG-UI-compatible events.
4. Session history is persisted after assistant completion or approval suspension.

### Thread restore

1. User selects a thread.
2. Frontend agent calls `/connect`.
3. Backend loads history from `SessionArchive`.
4. Backend emits `MESSAGES_SNAPSHOT`.
5. If the thread has a pending approval, backend also emits `CustomEvent("on_interrupt")`.

### Approval pause / resume

1. Model requests a gated tool.
2. Backend emits tool start / args.
3. Backend stores pending approval state.
4. Backend emits `CustomEvent("on_interrupt")` and ends the stream.
5. Frontend renders approval UI via `useInterrupt`.
6. User approves or rejects.
7. CopilotKit starts a new run with resume metadata.
8. Backend loads pending approval state and continues execution.

### Interrupt

1. User clicks stop.
2. Frontend calls `/interrupt` for the active run.
3. Backend marks the run controller interrupted.
4. Core checks the controller at safe boundaries and stops with a clean terminal event.

---

## Compatibility Notes

The current AG-UI ecosystem has an interrupt draft, but the shipped CopilotKit hook we are integrating with today reacts to `CustomEvent("on_interrupt")`. This phase therefore adopts:

- **current-compatible behavior:** CopilotKit `useInterrupt` via `CustomEvent`
- **future migration path:** map to native AG-UI interrupt semantics once the frontend and protocol packages converge

This keeps today's implementation working without painting the backend into a corner.

---

## Testing Strategy

We will cover three levels:

1. **Persistence and replay**
   - session archive can restore messages
   - session manager can rebuild a session from SQLite
   - thread list endpoint returns archived threads

2. **Runtime control**
   - interrupt endpoint stops an active run
   - approval suspension emits the expected interrupt event
   - approval resume continues execution

3. **Frontend integration**
   - React app builds successfully
   - backend serves the built app
   - browser path can switch threads and restore transcript via backend replay

---

## Tradeoffs

### Why not CopilotKit runtime `/info` + `useThreads`?

Because that would pull the backend toward CopilotKit's runtime contract and platform assumptions. We only need the React agent surface, not a second runtime abstraction.

### Why not keep the static HTML and just add buttons?

Because thread replay, tool rendering, and approval UI become much harder to evolve in a one-off hand-written page. CopilotKit already solves the agent chat surface well.

### Why is approval state only in memory?

Because durable resume across process restarts is a separate reliability feature. For this phase, in-process pause/resume already unlocks the main Web UX.

---

## Result

After this phase, `yi-min-ai` will have:

- a CopilotKit-based agent console
- backend-controlled stop / interrupt
- approval UI for gated tools
- thread replay from SQLite
- multi-thread switching without losing the existing Python runtime model
