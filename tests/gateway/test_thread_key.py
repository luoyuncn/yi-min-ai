"""NormalizedMessage thread key 测试。"""

from agent.gateway.normalizer import NormalizedMessage, build_thread_key, to_public_thread_id


def test_normalized_message_exposes_runtime_scoped_thread_key() -> None:
    """不同渠道实例的内部 thread key 应包含 channel_instance。"""

    message = NormalizedMessage(
        message_id="msg-1",
        session_id="oc_123",
        sender="user-1",
        body="你好",
        attachments=[],
        channel="feishu",
        channel_instance="feishu-main",
    )

    assert message.thread_key == "feishu:feishu-main:oc_123"


def test_build_thread_key_preserves_legacy_default_channel_namespace() -> None:
    """默认实例下已命名空间化的 session_id 应直接透传。"""

    assert build_thread_key("web:legacy", channel="web") == "web:legacy"


def test_to_public_thread_id_strips_default_web_runtime_prefix_only() -> None:
    """Web 默认 runtime 的内部 key 对外展示时应恢复成原始 thread_id。"""

    assert to_public_thread_id("web:default:thread-1", channel="web") == "thread-1"
    assert to_public_thread_id("web:legacy", channel="web") == "web:legacy"
