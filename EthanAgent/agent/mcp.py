"""MCP stdio client: connect servers from config and register ``mcp_<server>_<tool>`` tools."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import AsyncExitStack
from typing import Any

from loguru import logger

from config.schema import MCPServerConfig
from tools.base import Tool
from tools.tool_registry import ToolRegistry

_WINDOWS_SHELL_LAUNCHERS: frozenset[str] = frozenset(("npx", "npm", "pnpm", "yarn", "bunx"))


def _windows_command_basename(command: str) -> str:
    return command.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()


def _normalize_windows_stdio_command(
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
) -> tuple[str, list[str], dict[str, str] | None]:
    """Wrap Windows shell launchers so MCP stdio servers start reliably (same idea as nanobot)."""
    normalized_args = list(args or [])
    if os.name != "nt":
        return command, normalized_args, env

    basename = _windows_command_basename(command)
    if basename in {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return command, normalized_args, env

    if basename.endswith((".exe", ".com")):
        return command, normalized_args, env

    resolved = shutil.which(command, path=(env or {}).get("PATH")) or command
    resolved_basename = _windows_command_basename(resolved)
    should_wrap = (
        basename in _WINDOWS_SHELL_LAUNCHERS
        or basename.endswith((".cmd", ".bat"))
        or resolved_basename.endswith((".cmd", ".bat"))
    )
    if not should_wrap:
        return command, normalized_args, env

    comspec = (env or {}).get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
    return comspec, ["/d", "/c", command, *normalized_args], env


def _coerce_input_schema(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        out = dict(raw)
        out.setdefault("type", "object")
        if out.get("type") == "object":
            out.setdefault("properties", {})
            out.setdefault("required", [])
        return out
    return {"type": "object", "properties": {}, "required": []}


class MCPToolWrapper(Tool):
    """Single MCP tool exposed as EthanAgent ``Tool`` (name ``mcp_<server>_<tool>``)."""

    def __init__(
        self,
        session: Any,
        server_name: str,
        tool_def: Any,
        *,
        tool_timeout: int = 30,
    ):
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = (tool_def.description or tool_def.name or self._name).strip()
        self._parameters = _coerce_input_schema(getattr(tool_def, "inputSchema", None))
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def run(self, **kwargs: Any) -> Any:
        from mcp import types

        try:
            # self._session是mcp中的ClientSession
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '{}' timed out after {}s", self._name, self._tool_timeout)
            return f"(MCP tool '{self._name}' timed out after {self._tool_timeout}s)"
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '{}' was cancelled", self._name)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.exception("MCP tool '{}' failed: {}", self._name, exc)
            return f"Error: MCP tool '{self._name}': {type(exc).__name__}: {exc}"

        parts: list[str] = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) if parts else "(no output)"


async def connect_mcp_servers(
    mcp_servers: dict[str, MCPServerConfig],
    registry: ToolRegistry,
) -> dict[str, AsyncExitStack]:
    """Connect stdio MCP servers and register tools. Returns server_name -> stack for shutdown."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def connect_one(name: str, cfg: MCPServerConfig) -> tuple[str, AsyncExitStack | None]:
        if not cfg.enabled:
            return name, None
        if not (cfg.command or "").strip():
            logger.warning("MCP server '{}': empty command, skipping", name)
            return name, None

        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            command, args, env = _normalize_windows_stdio_command(cfg.command, cfg.args, cfg.env or None)
            params = StdioServerParameters(command=command, args=args, env=env)

            # 启动一个子进程来运行 MCP 服务器，并建立与它的通信通道 read/write。
            read, write = await stack.enter_async_context(stdio_client(params))
            # 进行握手，建立一个ClientSession
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            listed = await session.list_tools()
            tool_defs = list(getattr(listed, "tools", ()) or ())
            enabled = set(cfg.enabled_tools)
            allow_all = "*" in enabled
            registered = 0
            matched: set[str] = set()
            available_wrapped = [f"mcp_{name}_{t.name}" for t in tool_defs]

            for tool_def in tool_defs:
                wrapped = f"mcp_{name}_{tool_def.name}"
                if not allow_all and tool_def.name not in enabled and wrapped not in enabled:
                    continue
                registry.register(
                    MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                )
                registered += 1
                if tool_def.name in enabled:
                    matched.add(tool_def.name)
                if wrapped in enabled:
                    matched.add(wrapped)

            if enabled and not allow_all:
                unmatched = sorted(enabled - matched)
                if unmatched:
                    logger.warning(
                        "MCP server '{}': enabledTools not found: {}. Available: {}",
                        name,
                        ", ".join(unmatched),
                        ", ".join(available_wrapped) or "(none)",
                    )

            logger.info("MCP server '{}': connected, {} tool(s) registered", name, registered)
            return name, stack
        except Exception as e:
            hint = ""
            low = str(e).lower()
            if any(
                m in low
                for m in (
                    "parse error",
                    "invalid json",
                    "unexpected token",
                    "jsonrpc",
                    "content-length",
                )
            ):
                hint = " Hint: stdio servers must write JSON-RPC to stdout only; log to stderr."
            logger.error("MCP server '{}': failed to connect: {}{}", name, e, hint)
            try:
                await stack.aclose()
            except Exception:
                pass
            return name, None

    if not mcp_servers:
        return {}

    # Run each server in the *current* task so AsyncExitStack.aclose() matches
    # the task that entered stdio_client (avoids anyio cancel-scope errors on Windows).
    stacks: dict[str, AsyncExitStack] = {}
    for name, cfg in mcp_servers.items():
        try:
            srv_name, st = await connect_one(name, cfg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("MCP server '{}' connection failed: {}", name, e)
            continue
        if st is not None:
            stacks[srv_name] = st
    return stacks
