"""日志脱敏测试。"""

import logging

from agent.observability.logging import SensitiveDataFilter


def test_sensitive_filter_redacts_feishu_websocket_credentials() -> None:
    """飞书 websocket URL 中的一次性凭证不应明文写入日志。"""

    record = logging.LogRecord(
        name="Lark",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "connected to wss://msg-frontier.feishu.cn/ws/v2?"
            "access_key=abc123def456&ticket=550e8400-e29b-41d4-a716-446655440000"
        ),
        args=(),
        exc_info=None,
    )

    SensitiveDataFilter().filter(record)

    assert "access_key=***REDACTED***" in record.msg
    assert "ticket=***REDACTED***" in record.msg
    assert "abc123def456" not in record.msg
