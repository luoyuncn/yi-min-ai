"""FeishuAdapter 回归测试。"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from agent.gateway.adapters.feishu import FeishuAdapter


def test_feishu_adapter_runs_ws_client_with_thread_scoped_loop() -> None:
    """飞书 SDK 的全局 loop 应切换到线程内新建的事件循环。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    stale_loop = asyncio.new_event_loop()
    observed: dict[str, object] = {}
    fake_ws_module = SimpleNamespace(loop=stale_loop)
    adapter._ws_connected = threading.Event()

    class FakeWsClient:
        async def _connect(self) -> None:
            observed["current_loop"] = asyncio.get_running_loop()
            observed["sdk_loop"] = fake_ws_module.loop

        async def _ping_loop(self) -> None:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)
            return None

    adapter._lark = SimpleNamespace(ws=SimpleNamespace(client=fake_ws_module))
    adapter._ws_client = FakeWsClient()
    adapter._ws_loop = None

    adapter._run_ws_client()

    assert observed["current_loop"] is observed["sdk_loop"]
    assert observed["current_loop"] is not stale_loop
    assert fake_ws_module.loop is stale_loop
    assert adapter._ws_loop is None

    stale_loop.close()


def test_feishu_adapter_restores_sdk_loop_when_ws_start_fails() -> None:
    """即使 SDK 启动失败，也应恢复原始 loop 引用，避免污染后续调用。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    stale_loop = asyncio.new_event_loop()
    fake_ws_module = SimpleNamespace(loop=stale_loop)
    adapter._ws_connected = threading.Event()

    class FakeWsClient:
        async def _connect(self) -> None:
            raise RuntimeError("boom")

        async def _ping_loop(self) -> None:
            return None

    adapter._lark = SimpleNamespace(ws=SimpleNamespace(client=fake_ws_module))
    adapter._ws_client = FakeWsClient()
    adapter._ws_loop = None

    with pytest.raises(RuntimeError, match="boom"):
        adapter._run_ws_client()

    assert fake_ws_module.loop is stale_loop
    assert adapter._ws_loop is None

    stale_loop.close()


@pytest.mark.asyncio
async def test_feishu_adapter_connect_times_out_when_ws_never_reports_ready(monkeypatch) -> None:
    """如果后台线程一直未报告 ready，connect 应超时失败而不是假装连接成功。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.app_id = "app-id"
    adapter.app_secret = "app-secret"
    adapter._initialized = False
    adapter._message_queue = asyncio.Queue()
    adapter._lark = SimpleNamespace(
        EventDispatcherHandler=SimpleNamespace(
            builder=lambda *_: SimpleNamespace(
                register_p2_im_message_receive_v1=lambda *_: SimpleNamespace(
                    build=lambda: object()
                )
            )
        ),
        ws=SimpleNamespace(Client=lambda *_, **__: object()),
        Client=SimpleNamespace(
            builder=lambda: SimpleNamespace(
                app_id=lambda *_: SimpleNamespace(
                    app_secret=lambda *_: SimpleNamespace(build=lambda: object())
                )
            )
        ),
        LogLevel=SimpleNamespace(INFO="INFO"),
    )
    adapter._ws_connect_timeout_secs = 0.01

    def fake_run_ws_client() -> None:
        return None

    monkeypatch.setattr(adapter, "_run_ws_client", fake_run_ws_client)

    with pytest.raises(RuntimeError, match="timed out"):
        await adapter.connect()


def test_feishu_adapter_builds_lark_markdown_card() -> None:
    """飞书富文本回复应使用可渲染 Markdown 的卡片结构。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)

    card = adapter._build_markdown_card("**加粗**\n\n- 列表项", status="👀 已收到")

    assert card["config"]["wide_screen_mode"] is True
    assert card["elements"][0]["tag"] == "div"
    assert card["elements"][0]["text"]["tag"] == "lark_md"
    assert "👀 已收到" in card["elements"][0]["text"]["content"]
    assert "**加粗**" in card["elements"][0]["text"]["content"]
