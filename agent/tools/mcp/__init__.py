"""MCP (Model Context Protocol) Client 框架

一期预留，不接入外部 MCP Server。
二期接入时只需添加配置，无需改动代码。
"""

from agent.tools.mcp.client import MCPClient
from agent.tools.mcp.discovery import MCPDiscovery

__all__ = ["MCPClient", "MCPDiscovery"]
