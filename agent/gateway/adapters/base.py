"""通道适配器基础协议"""

from typing import Protocol, AsyncIterator
from agent.gateway.normalizer import NormalizedMessage


class ChannelAdapter(Protocol):
    """所有通道适配器的统一接口"""

    async def connect(self) -> None:
        """建立连接（WebSocket/HTTP/其他）"""
        ...

    async def receive(self) -> AsyncIterator[NormalizedMessage]:
        """接收消息流"""
        ...

    async def send(self, session_id: str, content: str) -> None:
        """发送纯文本消息"""
        ...

    async def send_rich(self, session_id: str, blocks: list[dict]) -> None:
        """发送富文本/卡片消息"""
        ...
