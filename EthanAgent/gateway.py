"""Gateway: central orchestrator that wires bus + channels + agent + cron."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from agent_runner import EthanAgentLoop, attach_cron_job_handler, register_dream_system_job
from bootstrap import build_agent_loop
from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus
from channels.manager import ChannelManager

if TYPE_CHECKING:
    from channels.base import BaseChannel


class Gateway:
    """Central orchestrator.
    Holds the message bus, agent loop, cron service, and channel manager.
    Runs the inbound worker that consumes messages from the bus and feeds
    them to the agent, then publishes responses back to the outbound queue.
    Usage (API mode)::
        gateway = Gateway(workspace)
        web_ch = WebChannel(gateway.bus)
        gateway.register_channel("web", web_ch)
        await gateway.start()
        # ... run FastAPI ...
        await gateway.stop()
    """
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.bus = MessageBus()
        self.agent_loop, self.cron = build_agent_loop(workspace,self.bus)
        self.channel_manager = ChannelManager(self.bus)
        self._agent_task: asyncio.Task[None] | None = None
        self._cli_session_key: str | None = None
        self._start_time: float | None = None

    # -- Channel registry ----------------------------------------------------
    def register_channel(self, name: str, channel: BaseChannel) -> None:
        self.channel_manager.register(name, channel)

    # -- Lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        """Start channel manager dispatch, agent loop, cron wiring, and cron."""
        import time
        now = time.time()
        self._start_time = now
        self.agent_loop._start_time = now
        attach_cron_job_handler(self.cron, self.agent_loop, message_bus=self.bus)
        register_dream_system_job(self.cron)
        await self.channel_manager.start()
        self._agent_task = asyncio.create_task(self.agent_loop._run_for_dispatch())
        await self.cron.start()
        logger.info("Gateway: started, workspace={}", self.workspace)

    async def stop(self) -> None:
        """Gracefully stop all components in reverse order."""
        self.agent_loop._running = False
        if self._agent_task is not None:
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass
            self._agent_task = None
        await self.channel_manager.stop()
        self.cron.stop()
        await self.agent_loop.close_mcp()
        logger.info("Gateway: stopped")


    # -- CLI mode ------------------------------------------------------------
    async def run_cli_loop(self) -> None:
        """Blocking CLI input loop (for standalone use without API)."""
        await self.agent_loop._connect_mcp()

        if self._cli_session_key is None:
            self._cli_session_key = (
                os.environ.get("ETHAN_SESSION_KEY")
                or f"cli:{uuid.uuid4().hex[:12]}"
            )
            logger.info(
                "Gateway CLI session: {} (export ETHAN_SESSION_KEY to reuse)",
                self._cli_session_key,
            )

        running = True
        while running:
            try:
                query = await asyncio.to_thread(
                    input, "\033[36ms01 >> \033[0m"
                )
            except (EOFError, KeyboardInterrupt):
                break

            query = query.strip()
            if not query:
                continue
            if query in ("/exit", "/quit"):
                break

            try:
                await self.bus.publish_inbound(
                    InboundMessage(
                        channel="cli",
                        chat_id=self._cli_session_key,
                        content=query,
                    )
                )
                reply = await self.bus.consume_outbound()
                print(reply.content if reply.content else "No response")
            except Exception as exc:
                logger.exception("CLI loop error")
                print(f"Error: {exc}")


# -- Standalone CLI entry point ----------------------------------------------


def run_cli() -> None:
    """Entry point for ``python gateway.py``."""
    workspace = Path(__file__).resolve().parent
    gateway = Gateway(workspace)

    async def _main() -> None:
        await gateway.start()
        try:
            await gateway.run_cli_loop()
        finally:
            await gateway.stop()

    asyncio.run(_main())


if __name__ == "__main__":
    run_cli()
