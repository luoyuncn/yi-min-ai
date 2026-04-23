"""Session 的内存数据模型。

这里的数据结构刻意保持很轻：
先让核心循环能稳定保存会话历史，
后面再逐步加状态机、摘要、恢复等能力。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class SessionMetadata:
    """描述一个会话的基础元信息。"""

    session_id: str
    channel: str
    created_at: datetime
    last_active_at: datetime
    message_count: int = 0


@dataclass(slots=True)
class Session:
    """运行中会话对象。

    `history` 里按时间顺序保存 user / assistant / tool 消息，
    后面 ContextAssembler 会直接使用这些历史消息重建上下文。
    """

    metadata: SessionMetadata
    history: list[dict] = field(default_factory=list)

    def append(self, message: dict) -> None:
        """向会话里追加一条消息，并同步更新计数与最后活跃时间。"""

        self.history.append(message)
        self.metadata.message_count += 1
        self.metadata.last_active_at = datetime.now(UTC)
