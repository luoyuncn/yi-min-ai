"""通道适配器模块"""

from agent.gateway.adapters.base import ChannelAdapter
from agent.gateway.adapters.feishu import FeishuAdapter

__all__ = ["ChannelAdapter", "FeishuAdapter"]
