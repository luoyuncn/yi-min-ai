"""FeishuAdapter 回归测试。"""

import asyncio
import builtins
import logging
import json
import threading
from types import SimpleNamespace

import pytest

from agent.gateway.adapters.feishu import FeishuAdapter


def test_feishu_adapter_logs_lark_sdk_import_duration(monkeypatch, caplog) -> None:
    """首次加载 lark-oapi 较慢时，启动日志应显示正在导入 SDK。"""

    original_import = builtins.__import__
    fake_lark = SimpleNamespace()

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lark_oapi":
            return fake_lark
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    caplog.set_level(logging.INFO, logger="agent.gateway.adapters.feishu")

    adapter = FeishuAdapter("app-id", "app-secret")

    assert adapter._lark is fake_lark
    assert "Loading lark-oapi SDK" in caplog.text
    assert "lark-oapi SDK loaded" in caplog.text


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


def test_feishu_adapter_converts_markdown_tables_to_bullets_for_cards() -> None:
    """飞书卡片应把 Markdown 表格降级成稳定可读的列表文本。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)

    card = adapter._build_markdown_card(
        "| 时间 | 商家 | 金额 |\n|------|------|------|\n| 早餐 | Tims | ¥15.00 |\n| 午餐 | 老乡鸡 | ¥27.00 |"
    )

    content = card["elements"][0]["text"]["content"]

    assert "|------|" not in content
    assert "- 时间：早餐；商家：Tims；金额：¥15.00" in content
    assert "- 时间：午餐；商家：老乡鸡；金额：¥27.00" in content


def test_feishu_adapter_ignores_duplicate_message_ids() -> None:
    """同一条飞书消息重复投递时，只应入队一次。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.app_id = "app-id"
    adapter.adapter_id = "feishu-main"
    adapter._message_queue = asyncio.Queue()

    event = SimpleNamespace(
        sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="user-open-id")),
        message=SimpleNamespace(
            message_id="om-msg-1",
            chat_id="chat-1",
            chat_type="p2p",
            message_type="text",
            content=json.dumps({"text": "你好"}, ensure_ascii=False),
            create_time="1713933600000",
        ),
    )
    data = SimpleNamespace(event=event)

    adapter._on_message_receive(data)
    adapter._on_message_receive(data)

    assert adapter._message_queue.qsize() == 1


def test_feishu_adapter_accepts_distinct_message_ids() -> None:
    """不同 message_id 的消息不应被误判为重复。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.app_id = "app-id"
    adapter.adapter_id = "feishu-main"
    adapter._message_queue = asyncio.Queue()

    def build_data(message_id: str, text: str):
        return SimpleNamespace(
            event=SimpleNamespace(
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="user-open-id")),
                message=SimpleNamespace(
                    message_id=message_id,
                    chat_id="chat-1",
                    chat_type="p2p",
                    message_type="text",
                    content=json.dumps({"text": text}, ensure_ascii=False),
                    create_time="1713933600000",
                ),
            )
        )

    adapter._on_message_receive(build_data("om-msg-1", "第一条"))
    adapter._on_message_receive(build_data("om-msg-2", "第二条"))

    assert adapter._message_queue.qsize() == 2


def test_feishu_adapter_extracts_text_from_post_message_with_link() -> None:
    """带链接的富文本消息也应被提取出可读正文，而不是被直接忽略。"""

    adapter = FeishuAdapter.__new__(FeishuAdapter)
    adapter.app_id = "app-id"
    adapter.adapter_id = "feishu-main"
    adapter._message_queue = asyncio.Queue()

    post_content = {
        "zh_cn": {
            "title": "",
            "content": [
                [
                    {"tag": "text", "text": "BaseUrl: "},
                    {
                        "tag": "a",
                        "text": "https://api.deepseek.com",
                        "href": "https://api.deepseek.com",
                    },
                ]
            ],
        }
    }
    data = SimpleNamespace(
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="user-open-id")),
            message=SimpleNamespace(
                message_id="om-msg-post-1",
                chat_id="chat-1",
                chat_type="p2p",
                message_type="post",
                content=json.dumps(post_content, ensure_ascii=False),
                create_time="1713933600000",
            ),
        )
    )

    adapter._on_message_receive(data)

    assert adapter._message_queue.qsize() == 1
    normalized = adapter._message_queue.get_nowait()
    assert normalized.body == "BaseUrl: https://api.deepseek.com"
