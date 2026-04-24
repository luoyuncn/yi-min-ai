# AG-UI Agent Web Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a local-first Web UI for `yi-min-ai` by exposing the current Python agent runtime through an AG-UI compatible SSE endpoint and a minimal in-repo browser agent console.

**Architecture:** Keep the existing `AgentCore` as the source of truth. Introduce a small internal runtime-event layer, a protocol adapter that maps runtime events to AG-UI event objects, and a FastAPI web app that serves both a static browser UI and an SSE endpoint. Preserve the current CLI behavior by making `run()` consume the new event stream internally.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, `ag-ui-protocol`, `python-dotenv`, pytest, existing provider/tool/session modules, plain HTML/CSS/JS for the first Web UI

---

### Task 1: Add dependencies and the web/runtime event scaffolding

**Files:**
- Modify: `pyproject.toml`
- Create: `agent/web/__init__.py`
- Create: `agent/web/events.py`
- Test: `tests/web/test_events.py`

**Step 1: Write the failing test**

```python
from agent.web.events import AssistantTextDeltaEvent, RunStartedEvent


def test_runtime_events_capture_basic_metadata() -> None:
    event = RunStartedEvent(thread_id="thread-1", run_id="run-1")
    delta = AssistantTextDeltaEvent(message_id="msg-1", delta="hello")

    assert event.thread_id == "thread-1"
    assert delta.delta == "hello"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_events.py -v`
Expected: FAIL with missing `agent.web.events`.

**Step 3: Write minimal implementation**

- Add `fastapi`, `uvicorn`, and `ag-ui-protocol` to `pyproject.toml`.
- Define focused dataclasses in `agent/web/events.py` for:
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

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_events.py -v`
Expected: PASS

### Task 2: Add event-stream execution to the core without breaking CLI

**Files:**
- Modify: `agent/core/loop.py`
- Test: `tests/core/test_loop_events.py`

**Step 1: Write the failing test**

```python
import asyncio

from agent.core.loop import AgentCore
from agent.gateway.normalizer import NormalizedMessage


class FakeProviderManager:
    async def call(self, request):
        if any(msg["role"] == "tool" for msg in request.messages):
            return type("Resp", (), {"type": "text", "text": "done", "tool_calls": None})()
        return type(
            "Resp",
            (),
            {
                "type": "tool_calls",
                "text": "",
                "tool_calls": [{"id": "tool-1", "name": "file_read", "input": {"path": "notes.txt"}}],
            },
        )()


def test_run_events_emits_tool_trace(tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("# Identity\nYi Min\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("# User Profile\n", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    core = AgentCore.build_for_test(tmp_path, FakeProviderManager())
    message = NormalizedMessage(
        message_id="1",
        session_id="thread-1",
        sender="user",
        body="读取 notes.txt",
        attachments=[],
        channel="web",
        metadata={},
    )

    async def collect():
        return [event async for event in core.run_events(message)]

    events = asyncio.run(collect())

    assert any(event.kind == "tool_call_started" for event in events)
    assert any(event.kind == "tool_call_result" for event in events)
    assert events[-1].kind == "run_finished"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_loop_events.py -v`
Expected: FAIL with missing `run_events`.

**Step 3: Write minimal implementation**

- Add `AgentCore.run_events(message)` as an async generator.
- Keep `run(message)` as a compatibility wrapper that consumes `run_events` and returns final text.
- Emit runtime events during:
  - run start
  - text response generation
  - tool start / args / result / end
  - run finish / error
- Preserve current persistence behavior.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_loop_events.py -v`
Expected: PASS

### Task 3: Add the AG-UI protocol adapter

**Files:**
- Create: `agent/web/ag_ui_adapter.py`
- Test: `tests/web/test_ag_ui_adapter.py`

**Step 1: Write the failing test**

```python
from agent.web.ag_ui_adapter import runtime_event_to_ag_ui
from agent.web.events import AssistantTextDeltaEvent, RunStartedEvent


def test_runtime_event_maps_to_ag_ui_payload() -> None:
    payload = runtime_event_to_ag_ui(
        RunStartedEvent(thread_id="thread-1", run_id="run-1")
    )
    delta = runtime_event_to_ag_ui(
        AssistantTextDeltaEvent(message_id="msg-1", delta="hello")
    )

    assert payload["type"] == "RUN_STARTED"
    assert payload["threadId"] == "thread-1"
    assert delta["type"] == "TEXT_MESSAGE_CONTENT"
    assert delta["delta"] == "hello"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_ag_ui_adapter.py -v`
Expected: FAIL with missing adapter.

**Step 3: Write minimal implementation**

- Map internal runtime events to AG-UI payload dictionaries.
- Use AG-UI field names:
  - `threadId`
  - `runId`
  - `messageId`
  - `toolCallId`
  - `toolCallName`
  - `delta`
  - `content`
- Use `EventEncoder` from `ag_ui.encoder` to encode SSE frames.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_ag_ui_adapter.py -v`
Expected: PASS

### Task 4: Add the FastAPI web app and streaming endpoint

**Files:**
- Create: `agent/web/app.py`
- Create: `agent/web/main.py`
- Test: `tests/web/test_app.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from agent.web.app import create_web_app


def test_web_app_serves_health_and_html(tmp_path) -> None:
    app = create_web_app(config_path=tmp_path / "config" / "agent.yaml", testing=True)
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    assert "text/html" in client.get("/").headers["content-type"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: FAIL with missing web app.

**Step 3: Write minimal implementation**

- Build `create_web_app(config_path: Path, testing: bool = False)`.
- Reuse `build_app()` from `agent.app`.
- Add:
  - `GET /`
  - `GET /api/health`
  - `POST /api/threads/{thread_id}/runs`
- The run endpoint should:
  - accept a small request body containing user text and optional `run_id`
  - convert to `NormalizedMessage`
  - stream AG-UI SSE events

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_app.py -v`
Expected: PASS

### Task 5: Add a minimal in-repo Agent Web UI

**Files:**
- Create: `agent/web/static/index.html`
- Modify: `agent/web/app.py`
- Test: `tests/web/test_ui_assets.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from agent.web.app import create_web_app


def test_root_page_contains_agent_ui_hooks(tmp_path) -> None:
    app = create_web_app(config_path=tmp_path / "config" / "agent.yaml", testing=True)
    client = TestClient(app)

    response = client.get("/")

    assert "Agent Timeline" in response.text
    assert "thread-id" in response.text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_ui_assets.py -v`
Expected: FAIL because the root page is missing the UI shell.

**Step 3: Write minimal implementation**

- Serve a static single-page HTML UI.
- Include:
  - thread id input
  - message input
  - transcript area
  - tool timeline area
  - JS that opens the SSE run stream and renders AG-UI events

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_ui_assets.py -v`
Expected: PASS

### Task 6: Add an end-to-end web integration test

**Files:**
- Test: `tests/integration/test_web_app.py`

**Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient

from agent.web.app import create_web_app


def test_web_run_streams_ag_ui_events(tmp_path) -> None:
    app = create_web_app(config_path=tmp_path / "config" / "agent.yaml", testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-1/runs",
        json={"text": "你好", "run_id": "run-1"},
    ) as response:
        body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in response.iter_text())

    assert response.status_code == 200
    assert "RUN_STARTED" in body
    assert "TEXT_MESSAGE" in body or "RUN_FINISHED" in body
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_web_app.py -v`
Expected: FAIL because the stream endpoint is incomplete.

**Step 3: Write minimal implementation**

- Ensure testing mode works through the web app path.
- Make the endpoint emit a complete SSE sequence.
- Verify stream termination behavior.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_web_app.py -v`
Expected: PASS

### Task 7: Update docs and startup instructions

**Files:**
- Modify: `README.md`
- Optionally create: `docs/plans/2026-04-23-ag-ui-agent-web-design.md`
- Optionally create: `docs/plans/2026-04-23-ag-ui-agent-web-implementation.md`

**Step 1: Add the doc updates**

- Document new local startup flow:
  - `uv sync`
  - `uv run python -m agent.web.main --config config/agent.yaml --testing`
- Explain that the first Web UI is a local agent console and that the backend event stream is AG-UI compatible.

**Step 2: Run targeted smoke verification**

Run: `uv run python -m agent.web.main --config config/agent.yaml --testing`
Expected: local web server starts and logs a URL.

### Task 8: Final verification

**Files:**
- Verify the whole repo

**Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass.

**Step 2: Run the Web app smoke test**

Run: `uv run python -m agent.web.main --config config/agent.yaml --testing`
Expected: app starts locally.

**Step 3: Manually verify**

- Open the root page in a browser.
- Send a normal message.
- Send a tool-triggering message such as `读取 SOUL.md`.
- Confirm transcript and tool timeline both update.

## Implementation Notes

- Keep the first slice local-first.
- Do not add auth, multi-user storage, or CopilotKit frontend code yet.
- Keep the backend contract protocol-clean so a future frontend can replace the static page without changing `AgentCore`.
- Preserve the existing CLI entrypoint and tests.

