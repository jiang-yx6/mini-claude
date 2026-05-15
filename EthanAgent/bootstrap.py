"""Shared construction of ``EthanAgentLoop`` + ``CronService`` (gateway and HTTP API)."""

from __future__ import annotations

import os
from pathlib import Path

from agent_runner import EthanAgentLoop
from config.loader import load_config
from cron.service import CronService
from providers.base import AnthropicProvider
from bus.queue import MessageBus

def build_agent_loop(workspace: Path, bus: MessageBus) -> tuple[EthanAgentLoop, CronService]:
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    api_base = os.getenv("ANTHROPIC_API_BASE", "https://api.deepseek.com/anthropic")
    model = os.getenv("MODEL_NAME", "deepseek-chat")

    cfg = load_config(workspace)
    mcp_servers = {n: s for n, s in cfg.tools.mcp_servers.items() if s.enabled}

    cron_store_path = workspace / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    loop = EthanAgentLoop(
        bus = bus,
        provider=AnthropicProvider(api_base=api_base, api_key=api_key),
        workspace=workspace,
        model=model,
        max_iterations=10,
        max_tool_result_chars=4000,
        context_block_limit=30,
        session_ttl_minutes=30,
        cron_service=cron,
        mcp_servers=mcp_servers,
    )
    return loop, cron
