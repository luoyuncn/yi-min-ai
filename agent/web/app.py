"""FastAPI Web 应用。"""

import json
from pathlib import Path
from uuid import uuid4

from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from agent.app import build_app
from agent.web.ag_ui_adapter import runtime_event_to_ag_ui
from agent.web.events import CustomEvent, MessagesSnapshotEvent, RunFinishedEvent, RunStartedEvent
from agent.web.runtime_state import PendingApprovalStore, RunControlRegistry


class RunRequest(BaseModel):
    """浏览器提交的一次 run 请求。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    text: str | None = None
    run_id: str | None = None
    runId: str | None = None
    messages: list[dict] | None = None
    state: dict | None = None
    forwardedProps: dict | None = None


def create_web_app(config_path: Path, testing: bool = False) -> FastAPI:
    """创建本地 Web Agent 应用。"""

    agent_app = build_app(config_path=config_path, testing=testing)
    encoder = EventEncoder()
    static_dir = Path(__file__).parent / "static"
    built_dir = static_dir / "app"
    index_file = built_dir / "index.html" if (built_dir / "index.html").exists() else static_dir / "index.html"
    run_controls = RunControlRegistry()
    approvals = PendingApprovalStore()

    app = FastAPI(title="yi-min-ai web")
    app.state.agent_app = agent_app
    app.state.run_controls = run_controls
    app.state.pending_approvals = approvals
    if (built_dir / "assets").exists():
        app.mount("/assets", StaticFiles(directory=built_dir / "assets"), name="frontend-assets")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return index_file.read_text(encoding="utf-8")

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/threads")
    async def list_threads() -> JSONResponse:
        items = []
        for row in agent_app.core.session_archive.list_sessions(limit=100):
            pending = approvals.get_by_thread(row["session_id"])
            items.append(
                {
                    "thread_id": row["session_id"],
                    "channel": row["channel"],
                    "message_count": row["message_count"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_message": row["last_message"],
                    "pending_approval": pending is not None,
                }
            )
        return JSONResponse({"items": items})

    @app.get("/api/threads/{thread_id}")
    async def get_thread(thread_id: str) -> JSONResponse:
        session = agent_app.core.session_archive.load_session(thread_id)
        if session is None:
            raise HTTPException(status_code=404, detail="thread not found")

        return JSONResponse(
            {
                "thread_id": thread_id,
                "message_count": session.metadata.message_count,
                "messages": session.history,
                "pending_approval": approvals.get_by_thread(thread_id) is not None,
            }
        )

    @app.post("/api/threads/{thread_id}/connect")
    async def connect_thread(thread_id: str, request: Request) -> StreamingResponse:
        payload = await _read_request_payload(request)
        run_id = payload.get("run_id") or payload.get("runId") or str(uuid4())

        async def event_stream():
            session = agent_app.core.session_archive.load_session(thread_id)
            history = session.history if session is not None else []
            yield encoder.encode(
                runtime_event_to_ag_ui(RunStartedEvent(thread_id=thread_id, run_id=run_id))
            )
            yield encoder.encode(runtime_event_to_ag_ui(MessagesSnapshotEvent(messages=history)))
            pending = approvals.get_by_thread(thread_id)
            if pending is not None:
                yield encoder.encode(
                    runtime_event_to_ag_ui(
                        CustomEvent(
                            name="on_interrupt",
                            value={
                                "approval_id": pending.approval_id,
                                "thread_id": pending.thread_id,
                                "run_id": pending.run_id,
                                "tool_name": pending.tool_call["name"],
                                "tool_call_id": pending.tool_call["id"],
                                "args": pending.tool_call["input"],
                                "message": pending.message,
                            },
                        )
                    )
                )
            yield encoder.encode(
                runtime_event_to_ag_ui(
                    RunFinishedEvent(thread_id=thread_id, run_id=run_id, result_text="")
                )
            )

        return StreamingResponse(
            event_stream(),
            media_type=encoder.get_content_type(),
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/threads/{thread_id}/runs/{run_id}/interrupt")
    async def interrupt_run(thread_id: str, run_id: str) -> JSONResponse:
        control = run_controls.get(run_id)
        if control is None or control.thread_id != thread_id:
            raise HTTPException(status_code=404, detail="run not found")
        control.interrupt()
        return JSONResponse({"status": "interrupted", "thread_id": thread_id, "run_id": run_id})

    @app.post("/api/threads/{thread_id}/runs")
    async def create_run(thread_id: str, payload: RunRequest) -> StreamingResponse:
        run_id = payload.run_id or payload.runId or str(uuid4())
        command = _normalize_command((payload.forwardedProps or {}).get("command"))
        text = payload.text if payload.text is not None else _extract_text(payload.messages or [])
        control = run_controls.start(thread_id=thread_id, run_id=run_id)

        async def event_stream():
            try:
                async for event in agent_app.stream_events(
                    text,
                    session_id=thread_id,
                    sender="web-user",
                    channel="web",
                    metadata={"run_id": run_id, "command": command} if command else {"run_id": run_id},
                    runtime_control=control,
                    approval_store=approvals,
                ):
                    yield encoder.encode(runtime_event_to_ag_ui(event))
            finally:
                run_controls.finish(run_id)

        return StreamingResponse(
            event_stream(),
            media_type=encoder.get_content_type(),
            headers={"Cache-Control": "no-cache"},
        )

    return app


def _extract_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(part for part in text_parts if part)
        return json.dumps(content, ensure_ascii=False)
    return ""


def _normalize_command(command: dict | None) -> dict | None:
    if not command:
        return None
    normalized = dict(command)
    if "interruptEvent" in normalized and "interrupt_event" not in normalized:
        normalized["interrupt_event"] = normalized["interruptEvent"]
    return normalized


async def _read_request_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return {}
    except RuntimeError:
        return {}
    return payload if isinstance(payload, dict) else {}
