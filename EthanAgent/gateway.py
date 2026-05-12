import os
from pathlib import Path
import asyncio
from agent_runner import EthanAgentLoop, attach_cron_job_handler, register_dream_system_job
from config.loader import load_config
from providers.base import AnthropicProvider
from cron.service import CronService

if __name__ == "__main__":
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    api_base = os.getenv("ANTHROPIC_API_BASE", "https://api.deepseek.com/anthropic")
    model = os.getenv("MODEL_NAME", "deepseek-chat")
    workspace = Path(__file__).resolve().parent

    cfg = load_config(workspace) 
    mcp_servers = {n: s for n, s in cfg.tools.mcp_servers.items() if s.enabled}

    cron_store_path = workspace / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    loop = EthanAgentLoop(
        provider=AnthropicProvider(api_base=api_base, api_key=api_key),
        workspace=workspace,
        model=model,
        max_iterations=10,
        max_tool_result_chars=4000,
        context_block_limit=30,
        session_ttl_minutes=30,
        cron_service = cron,
        mcp_servers=mcp_servers,
    )

    attach_cron_job_handler(cron, loop)
    register_dream_system_job(cron)

    async def main():
        try:
            await loop._connect_mcp()
            await asyncio.gather(
                loop._run_for_dispatch(),
                cron.start(),
            )
        finally:
            await loop.close_mcp()
            cron.stop()

    asyncio.run(main())