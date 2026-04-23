"""Web 集成测试。"""

from fastapi.testclient import TestClient

from agent.web.app import create_web_app
from tests.web.support import write_testing_config


def test_web_run_streams_ag_ui_events(tmp_path) -> None:
    """Web 入口应能把一次 agent run 以 AG-UI SSE 流式发出。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-1/runs",
        json={"text": "你好", "run_id": "run-1"},
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    assert response.status_code == 200
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body


def test_web_lists_threads_and_replays_thread_history(tmp_path) -> None:
    """线程列表和 connect replay 是多会话切换的基础。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-1/runs",
        json={"text": "你好", "run_id": "run-1"},
    ) as response:
        _ = "".join(chunk for chunk in response.iter_text())

    threads_response = client.get("/api/threads")
    connect_response = client.post("/api/threads/thread-1/connect", json={})

    assert threads_response.status_code == 200
    assert any(item["thread_id"] == "thread-1" for item in threads_response.json()["items"])
    assert connect_response.status_code == 200
    assert "RUN_STARTED" in connect_response.text
    assert "MESSAGES_SNAPSHOT" in connect_response.text
    assert "你好" in connect_response.text
    assert "RUN_FINISHED" in connect_response.text


def test_web_run_accepts_ag_ui_style_payload(tmp_path) -> None:
    """CopilotKit / HttpAgent 会发送 AG-UI 风格的 RunAgentInput。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-agui/runs",
        json={
            "runId": "run-1",
            "messages": [{"id": "user-1", "role": "user", "content": "你好"}],
            "state": {},
            "forwardedProps": {},
        },
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    assert response.status_code == 200
    assert "RUN_STARTED" in body
    assert "RUN_FINISHED" in body


def test_web_interrupt_endpoint_marks_active_run(tmp_path) -> None:
    """停止按钮需要能通过 Web API 标记当前 run 已被 interrupt。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    control = app.state.run_controls.start(thread_id="thread-stop", run_id="run-stop")
    client = TestClient(app)

    response = client.post("/api/threads/thread-stop/runs/run-stop/interrupt")

    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert control.is_interrupted


def test_web_connect_replays_pending_approval_interrupt(tmp_path) -> None:
    """刷新页面后 reconnect 同一线程时，应能把待审批状态重新发给前端。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-approval/runs",
        json={"text": "写入 notes.txt", "run_id": "run-1"},
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    connect_response = client.post("/api/threads/thread-approval/connect", json={})

    assert response.status_code == 200
    assert "CUSTOM" in body
    assert "on_interrupt" in body
    assert connect_response.status_code == 200
    assert "RUN_STARTED" in connect_response.text
    assert "MESSAGES_SNAPSHOT" in connect_response.text
    assert "on_interrupt" in connect_response.text
    assert "RUN_FINISHED" in connect_response.text


def test_web_run_can_resume_pending_approval_from_ag_ui_payload(tmp_path) -> None:
    """CopilotKit useInterrupt 会通过 forwardedProps.command.interruptEvent 恢复。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/threads/thread-approval/runs",
        json={"text": "写入 notes.txt", "run_id": "run-1"},
    ) as response:
        _ = "".join(chunk for chunk in response.iter_text())

    pending = app.state.pending_approvals.get_by_thread("thread-approval")

    assert pending is not None

    with client.stream(
        "POST",
        "/api/threads/thread-approval/runs",
        json={
            "runId": "run-2",
            "messages": [{"id": "resume-msg", "role": "user", "content": ""}],
            "state": {},
            "forwardedProps": {
                "command": {
                    "resume": {"approved": True},
                    "interruptEvent": {"approval_id": pending.approval_id},
                }
            },
        },
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    assert response.status_code == 200
    assert "TOOL_CALL_RESULT" in body
    assert "RUN_FINISHED" in body
    assert (tmp_path / "workspace" / "notes.txt").read_text(encoding="utf-8") == "hello from testing approval"


def test_web_connect_replays_legacy_thread_history_without_message_ids(tmp_path) -> None:
    """旧会话缺少 message id 时，connect 也应稳定恢复历史。"""

    app = create_web_app(config_path=write_testing_config(tmp_path), testing=True)
    app.state.agent_app.core.session_archive.append_turn(
        "legacy-thread",
        0,
        "user",
        "你好",
        payload={"role": "user", "content": "你好"},
    )
    app.state.agent_app.core.session_archive.append_turn(
        "legacy-thread",
        1,
        "assistant",
        "欢迎回来",
        payload={"role": "assistant", "content": "欢迎回来"},
    )
    client = TestClient(app)

    connect_response = client.post("/api/threads/legacy-thread/connect", json={})

    assert connect_response.status_code == 200
    assert "MESSAGES_SNAPSHOT" in connect_response.text
    assert "欢迎回来" in connect_response.text
