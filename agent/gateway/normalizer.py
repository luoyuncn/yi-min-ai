"""标准化消息对象。

不论消息来自 CLI、未来的飞书，还是内部调度，
只要先变成 `NormalizedMessage`，核心循环就能统一处理。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


def build_thread_key(
    session_id: str,
    *,
    channel: str,
    channel_instance: str = "default",
) -> str:
    """构造内部使用的 runtime 级线程主键。"""

    if channel_instance == "default" and session_id.startswith(f"{channel}:"):
        return session_id
    return f"{channel}:{channel_instance}:{session_id}"


def to_public_thread_id(
    thread_key: str,
    *,
    channel: str,
    channel_instance: str = "default",
) -> str:
    """把内部 thread key 还原成对外展示的线程 ID。"""

    prefix = f"{channel}:{channel_instance}:"
    if thread_key.startswith(prefix):
        return thread_key[len(prefix) :]
    return thread_key


@dataclass(slots=True)
class NormalizedMessage:
    """阶段一的统一消息格式。"""

    message_id: str
    session_id: str
    sender: str
    body: str
    attachments: list = field(default_factory=list)
    channel: str = "cli"
    channel_instance: str = "default"
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def thread_key(self) -> str:
        """返回内部使用的 runtime 级线程主键。"""

        return build_thread_key(
            self.session_id,
            channel=self.channel,
            channel_instance=self.channel_instance,
        )
