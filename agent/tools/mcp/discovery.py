"""MCP Server 自动发现与注册

一期预留，二期接入时实现。
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from agent.tools.mcp.client import MCPClient, MCPServerConfig
from agent.tools.registry import ToolRegistry, ToolDefinition

logger = logging.getLogger(__name__)


class MCPDiscovery:
    """MCP Server 自动发现与注册
    
    职责:
    - 读取 config/mcp_servers.yaml
    - 连接所有配置的 MCP Server
    - 获取工具列表并注册到 ToolRegistry
    """

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.clients: dict[str, MCPClient] = {}

    async def discover_and_register(
        self, registry: ToolRegistry
    ) -> dict[str, MCPClient]:
        """发现并注册所有 MCP Server 的工具
        
        Returns:
            成功连接的 MCP Client 字典
        """
        if not self.config_path.exists():
            logger.info(f"MCP config not found: {self.config_path}")
            return {}

        try:
            with open(self.config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            servers = config.get("servers", {})
            if not servers:
                logger.info("No MCP servers configured")
                return {}

            for server_name, server_config in servers.items():
                try:
                    await self._register_server(
                        server_name, server_config, registry
                    )
                except Exception as e:
                    logger.warning(
                        f"MCP: failed to connect {server_name}: {e}"
                    )

            logger.info(f"MCP: registered {len(self.clients)} servers")
            return self.clients

        except Exception as e:
            logger.error(f"MCP discovery failed: {e}", exc_info=True)
            return {}

    async def _register_server(
        self, name: str, config: dict, registry: ToolRegistry
    ) -> None:
        """注册单个 MCP Server"""
        logger.info(f"MCP: connecting to {name}...")

        server_config = MCPServerConfig(
            name=name,
            transport=config["transport"],
            command=config.get("command"),
            args=config.get("args"),
            env=config.get("env"),
            url=config.get("url"),
            requires_approval=config.get("requires_approval", False),
        )

        client = MCPClient(server_config)
        await client.connect()

        # 获取工具列表
        tools = await client.list_tools()

        # 注册到 ToolRegistry
        for tool in tools:
            tool_name = f"mcp_{name}_{tool.name}"

            registry.register(
                ToolDefinition(
                    name=tool_name,
                    description=f"[MCP:{name}] {tool.description}",
                    schema=self._build_schema(tool_name, tool),
                    handler=lambda params, c=client, t=tool: c.call_tool(
                        t.name, params
                    ),
                )
            )

        self.clients[name] = client
        logger.info(f"MCP: registered {len(tools)} tools from {name}")

    def _build_schema(self, tool_name: str, tool_def: Any) -> dict:
        """构建工具 schema"""
        return {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_def.description,
                "parameters": tool_def.input_schema,
            },
        }

    async def disconnect_all(self) -> None:
        """断开所有 MCP 连接"""
        for name, client in self.clients.items():
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting MCP {name}: {e}")

        self.clients.clear()
