# AG-UI Agent Web Integration Design

**Date:** 2026-04-23  
**Status:** Approved for implementation  
**Decision:** Preserve the existing Python agent runtime and add an AG-UI compatible web adapter plus a minimal browser-based agent console.

---

## Context

The current project is a runnable single-channel CLI agent. Its runtime is already cleanly separated into:

- gateway normalization
- core ReAct loop
- provider abstraction
- safe tool registry
- session archive and always-on memory

That makes the system a good candidate for a protocol adapter instead of a runtime rewrite.

The user explicitly chose the "preserve Python agent, add AG-UI / CopilotKit adapter" route rather than adopting LangGraph as the primary runtime. The design therefore optimizes for:

- reusing the existing `AgentCore`
- exposing agent execution as a structured event stream
- supporting a future CopilotKit / AG-UI frontend without forcing a JS backend rewrite
- shipping a first usable Web UI in this repository now

---

## Goals

Build a first Web interaction layer that lets a user talk to the agent in the browser and observe agent-style execution, not just plain chat text.

The first slice must provide:

- a browser-accessible Web UI
- a backend HTTP API
- AG-UI style streaming events over SSE
- visible assistant text streaming
- visible tool-call timeline
- stable thread identity across requests
- zero dependency on LangGraph

The first slice does **not** need to provide:

- a full CopilotKit frontend
- multi-user auth
- persistent run replay
- interrupts / approval resume protocol
- rich multimodal attachments
- a React build pipeline

---

## Alternatives Considered

### Option A: Open WebUI / LibreChat compatibility only

This would be the fastest route to "something in a browser", but it would bias the system toward chat-completions compatibility instead of agent-native UI semantics. It also does not naturally model step events, tool traces, or future interrupt/resume workflows.

**Why not chosen:** good as a temporary shell, weak as the long-term control surface for this agent.

### Option B: Rebuild backend around LangGraph and use LangChain Agent Chat UI

This would yield the richest off-the-shelf agent console, but it would require the current runtime to move toward LangGraph thread/checkpoint semantics. That would change the backend architecture materially and create migration pressure before the current runtime has matured.

**Why not chosen:** too much architectural displacement for the current stage.

### Option C: Preserve Python runtime, add AG-UI adapter and minimal in-repo Web console

This option keeps the current runtime intact, adds an event model that maps well to agent execution, and creates a future path to CopilotKit or any AG-UI client. It also keeps implementation scope realistic for the current repository.

**Chosen because:** it gives us a real Agent-type Web UI without rewriting the runtime.

---

## Architecture

The implementation adds a new web boundary but does not change the core ownership model.

```text
Browser UI
  -> FastAPI Web App
    -> AG-UI HTTP/SSE Adapter
      -> AgentApplication / AgentCore
        -> ProviderManager
        -> ToolRegistry
        -> SessionArchive / AlwaysOnMemory
```

Two new layers are introduced:

1. **Internal agent event layer**
   The current `AgentCore.run()` returns only the final text. To support an Agent UI, the core must also emit structured runtime events such as:
   - run started / finished
   - assistant text started / appended / finished
   - tool call started / args / result / finished
   - error

2. **AG-UI transport adapter**
   A web adapter will translate those internal runtime events into AG-UI protocol events and stream them as SSE.

This keeps the core independent from any single frontend protocol. AG-UI becomes an outer contract, not the runtime's internal truth.

---

## Data Flow

### 1. Browser request

The browser sends a POST request to a web endpoint with:

- `threadId`
- user text
- optional `runId`

The backend converts this input into the existing normalized message shape and resolves the session/thread mapping.

### 2. Agent execution

The core loads:

- `SOUL.md`
- `MEMORY.md`
- skill index
- session history

Then it executes the existing ReAct loop.

### 3. Event emission

During execution, the runtime emits internal events:

- `run_started`
- `assistant_text_started`
- `assistant_text_delta`
- `tool_call_started`
- `tool_call_args`
- `tool_call_result`
- `assistant_text_finished`
- `run_finished`

### 4. Protocol adaptation

The web layer maps those events to AG-UI SSE events such as:

- `RUN_STARTED`
- `STEP_STARTED`
- `TEXT_MESSAGE_START`
- `TEXT_MESSAGE_CONTENT`
- `TEXT_MESSAGE_END`
- `TOOL_CALL_START`
- `TOOL_CALL_ARGS`
- `TOOL_CALL_RESULT`
- `TOOL_CALL_END`
- `STEP_FINISHED`
- `RUN_FINISHED`
- `RUN_ERROR`

### 5. Frontend rendering

The browser consumes SSE incrementally and updates:

- transcript
- tool timeline
- run status
- error state

---

## Backend Design

### Internal event model

Add a new runtime event module with strongly typed dataclasses for execution-time events. This module belongs beside the core, not inside the web adapter, because future Feishu approval flows and debug tooling can also consume it.

Planned event families:

- `RunStartedEvent`
- `RunFinishedEvent`
- `RunErrorEvent`
- `AssistantTextStartEvent`
- `AssistantTextDeltaEvent`
- `AssistantTextEndEvent`
- `ToolCallStartEvent`
- `ToolCallArgsEvent`
- `ToolCallResultEvent`
- `ToolCallEndEvent`

### Agent core execution API

Retain `AgentCore.run()` for existing CLI behavior, but implement it in terms of a new async generator:

- `AgentCore.run_events(message) -> AsyncIterator[AgentRuntimeEvent]`

`run()` will consume the generator and return the final text for backward compatibility.

This design avoids duplicating execution logic between CLI and Web.

### Web application

Add a FastAPI app with:

- `GET /` -> minimal agent console HTML
- `POST /api/threads/{thread_id}/runs` -> SSE event stream
- `GET /api/health` -> health probe

The SSE endpoint will accept a small JSON payload containing user text and optionally an explicit `run_id`.

### Thread model

AG-UI uses `threadId` and `runId`. The current runtime already has `session_id`. In the first slice:

- `threadId` maps directly to `session_id`
- `runId` is generated per request if omitted

This mapping is simple, reversible, and compatible with the current session model.

---

## Frontend Design

The first in-repo Web UI is intentionally small and protocol-oriented. It is not meant to be the final product frontend.

It will provide:

- a thread input
- a chat transcript
- a "tool activity" panel
- streaming assistant output
- error banner
- a single-page, no-build static UI

This choice is deliberate:

- it avoids introducing Node and frontend bundling immediately
- it validates the backend protocol contract first
- it keeps the migration path open for CopilotKit later

The UI will render agent behavior in two columns:

- left: conversation transcript
- right: run timeline / tool events

That makes it meaningfully more agent-like than the current CLI while remaining light enough for this repository stage.

---

## Error Handling

The web adapter must fail in a way a browser UI can render cleanly.

Rules:

- Config or provider initialization errors return normal HTTP error responses before streaming starts.
- Runtime errors during execution emit `RUN_ERROR` and end the stream.
- Tool failures remain tool results when the runtime already models them that way; they should not automatically become transport failures.

The UI should keep the previous transcript visible and append an error card instead of replacing the whole page state.

---

## Security and Scope Boundaries

This design does not widen tool permissions.

The Web UI uses the exact same runtime and tool registry as CLI. Therefore:

- file tools remain workspace-scoped
- memory writes still target `MEMORY.md`
- session archive remains local SQLite
- no extra tool surface is introduced in the first slice

Because the first version is local-first, there is no auth layer yet. That is acceptable only because this slice is meant for local development usage.

---

## Testing Strategy

The implementation should be test-driven around three seams:

1. **Runtime event emission**
   Verify that a tool-using run emits the expected ordered events.

2. **AG-UI encoding**
   Verify that internal runtime events are converted into correct AG-UI event payloads.

3. **Web integration**
   Verify that the FastAPI app serves the HTML page and streams AG-UI SSE events from a test run.

CLI tests must remain green to prove the compatibility layer did not regress the existing entrypoint.

---

## Delivery Slice

The first implementation slice will stop at:

- local FastAPI server
- AG-UI compatible SSE stream
- minimal browser agent console
- visible assistant text and tool trace

It will intentionally defer:

- CopilotKit frontend integration
- interrupts / approvals in Web UI
- resumable runs
- state snapshots / deltas
- multi-user session management

That keeps the first slice small, useful, and aligned with YAGNI.

---

## Migration Path

After this slice, the system can evolve in three directions without architectural rework:

1. Replace the static HTML with a CopilotKit frontend while preserving the same AG-UI backend contract.
2. Add richer event families such as reasoning, state snapshots, and approval interrupts.
3. Introduce deployable auth/reverse-proxy concerns without changing the core runtime.

This is why AG-UI is the right seam for the project now: it turns the current runtime into a frontend-addressable agent without forcing a backend rewrite.
