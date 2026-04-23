"""MCP 协议客户端 - 支持 stdio / sse / http 三种传输模式

一期预留框架，等待 MCP 生态成熟后启用。
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP Server 配置"""

    name: str
    transport: str  # "stdio" | "sse" | "http"
    command: Optional[str] = None  # stdio 模式：启动命令
    args: Optional[list[str]] = None  # stdio 模式：命令参数
    env: Optional[dict[str, str]] = None  # stdio 模式：环境变量
    url: Optional[str] = None  # sse/http 模式：服务 URL
    requires_approval: bool = False  # 是否需要审批


@dataclass
class MCPToolDef:
    """MCP 工具定义"""

    name: str
    description: str
    input_schema: dict

    @classmethod
    def from_dict(cls, data: dict) -> "MCPToolDef":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
        )


class MCPClient:
    """MCP 协议客户端 - 支持 stdio / sse / http 三种传输模式
    
    注意：一期预留框架，暂不实现具体传输逻辑。
    二期接入时参考 Anthropic MCP SDK。
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._transport = None
        self._connected = False

    async def connect(self) -> None:
        """根据配置建立与 MCP Server 的连接"""
        logger.info(f"MCP Client: connecting to {self.config.name} (预留)")

        # TODO: 实现具体传输层
        # if self.config.transport == "stdio":
        #     self._transport = StdioTransport(...)
        # elif self.config.transport == "sse":
        #     self._transport = SSETransport(...)
        # elif self.config.transport == "http":
        #     self._transport = HttpTransport(...)

        self._connected = True
        logger.info(f"MCP Client: connected to {self.config.name} (预留)")

    async def disconnect(self) -> None:
        """断开连接"""
        if self._transport:
            # await self._transport.close()
            pass

        self._connected = False
        logger.info(f"MCP Client: disconnected from {self.config.name}")

    async def list_tools(self) -> list[MCPToolDef]:
        """获取 Server 暴露的所有工具定义"""
        if not self._connected:
            raise RuntimeError("MCP Client not connected")

        # TODO: 发送 tools/list 请求
        # response = await self._transport.send("tools/list", {})
        # return [MCPToolDef.from_dict(t) for t in response["tools"]]

        logger.debug(f"MCP Client: list_tools from {self.config.name} (预留)")
        return []

    async def call_tool(self, tool_name: str, params: dict) -> str:
        """调用指定工具"""
        if not self._connected:
            raise RuntimeError("MCP Client not connected")

        # TODO: 发送 tools/call 请求
        # response = await self._transport.send(
        #     "tools/call",
        #     {"name": tool_name, "arguments": params}
        # )
        # return response["content"]

        logger.debug(
            f"MCP Client: call_tool {tool_name} from {self.config.name} (预留)"
        )
        return f"MCP tool {tool_name} not yet implemented"


# TODO: 实现具体传输层
# class StdioTransport:
#     """Stdio 传输层（通过子进程 stdin/stdout 通信）"""
#     pass
#
# class SSETransport:
#     """SSE 传输层（Server-Sent Events）"""
#     pass
#
# class HttpTransport:
#     """HTTP 传输层（REST API）"""
#     pass
