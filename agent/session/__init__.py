"""Session 模块的公开入口。

一期的 Session 只负责一件事：
在单进程里把“同一个 session_id 的连续对话”保存住。
它还没有做恢复、清理、群聊隔离，那些留到后续阶段。
"""

from agent.session.manager import SessionManager
from agent.session.models import Session, SessionMetadata

__all__ = ["Session", "SessionManager", "SessionMetadata"]
