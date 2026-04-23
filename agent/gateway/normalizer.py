"""标准化消息对象。

不论消息来自 CLI、未来的飞书，还是内部调度，
只要先变成 `NormalizedMessage`，核心循环就能统一处理。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class NormalizedMessage:
    """阶段一的统一消息格式。"""

    message_id: str
    session_id: str
    sender: str
    body: str
    attachments: list = field(default_factory=list)
    channel: str = "cli"
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
