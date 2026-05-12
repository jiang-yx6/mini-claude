"""EthanAgent workspace configuration (e.g. MCP servers)."""

from config.schema import Config, MCPServerConfig, ToolsConfig
from config.loader import load_config

__all__ = ["Config", "MCPServerConfig", "ToolsConfig", "load_config"]
