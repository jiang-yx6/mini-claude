"""Pydantic models for EthanAgent ``config.json`` (nanobot-style ``tools.mcpServers``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    """One stdio MCP server entry."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = Field(default=30, alias="toolTimeout")
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"], alias="enabledTools")
    enabled: bool = True


class ToolsConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict, alias="mcpServers")


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tools: ToolsConfig = Field(default_factory=ToolsConfig)
