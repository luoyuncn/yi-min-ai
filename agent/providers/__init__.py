"""Provider 实现集合。

当前只真正实现了 Anthropic，
但包结构已经按“多 Provider”方式组织好了。
"""

from agent.providers.anthropic import AnthropicProvider

__all__ = ["AnthropicProvider"]
