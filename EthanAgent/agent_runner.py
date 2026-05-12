from pydantic import BaseModel, ConfigDict
from pathlib import Path
import asyncio
import copy
from contextlib import AsyncExitStack
import re
from datetime import datetime, timedelta

from tools.tool_registry import ToolRegistry
from typing import Any, Callable, final
from pathlib import Path
from agent.context import ContextBuilder
from agent.memory import Consolidator, Dream, MemoryStore, MilvusMemoryStore
from agent.compact import Compactor
from utils.runtime import build_length_recovery_message
from tools.file import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from tools.web_fetch import WebFetchTool
from tools.shell import ShellTool
from tools.cron import CronTool, LOCAL_TZ
from commands.router import CommandRouter
from commands.commands import register_commands
from session.manager import SessionManager, Session
from providers.base import LLMProvider, AnthropicProvider, LLMResponse
from loguru import logger
import os
import dotenv
import json

from cron.service import CronService
from cron.types import CronJob, CronPayload, CronSchedule
from config.schema import MCPServerConfig

dotenv.load_dotenv()


_MAX_LENGTH_RECOVERIES = 3

# Anthropic-style messages: tool results live in ``user`` content blocks (type tool_result).
_MICROCOMPACT_KEEP_RECENT = 10
_MICROCOMPACT_MIN_CHARS = 500
_COMPACTABLE_TOOLS = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "shell",
    "web_fetch",
})

_DEFAULT_DREAM_INTERVAL_MS = 2 * 3600_000 # 2 hours


def register_dream_system_job(
    cron: CronService,
    *,
    every_ms: int = _DEFAULT_DREAM_INTERVAL_MS,
) -> CronJob:
    """Idempotent Dream cron job (``system_event``). Use ``every`` schedule to avoid IANA tz on Windows."""
    return cron.register_system_job(
        CronJob(
            id="dream",
            name="dream",
            schedule=CronSchedule(kind="every", every_ms=every_ms),
            payload=CronPayload(kind="system_event", message="", deliver=False),
        )
    )


def attach_cron_job_handler(cron: CronService, loop: "EthanAgentLoop") -> None:
    """Wire ``cron.on_job``: Dream runs ``loop.dream.run()``; other jobs use ``process_direct``."""

    async def on_cron_job(job: CronJob) -> str | None:
        if job.name == "dream":
            try:
                await loop.dream.run()
                logger.info("Dream cron job completed")
            except Exception:
                logger.exception("Dream cron job failed")
            return None

        cron_tool = loop.tools.get("cron")
        cron_token = None

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)

        try:
            resp = await loop.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
            )

        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        response = resp if resp else ""
        logger.info("Cron job response: {}", response)

        return response

    cron.on_job = on_cron_job


class AgentRunSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    workspace: Path
    max_iterations: int
    max_tokens: int | None = None
    temperature: float | None = None

    checkpoint_callback: Callable[[dict[str, Any]], Any] | None = None

class EthanAgentLoop:   
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        max_tool_result_chars: int | None = None,
        context_block_limit: int | None = None,
        session_ttl_minutes: int = 30,
        cron_service: CronService | None = None,
        mcp_servers: dict[str, MCPServerConfig] | None = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations or 10
        self.max_tokens = None
        self.temperature = None
        self.context_block_limit = context_block_limit or 40
        self.max_tool_result_chars = max_tool_result_chars or 4000
        self._background_tasks: list[asyncio.Task] = []

        self.tools = ToolRegistry()
        self.cron_service = cron_service
        self.runner = AgentRunner(provider)
        self._register_default_tools(self.tools)
        self.commands = CommandRouter()
        register_commands(self.commands)

        self.session_locks: dict[str, asyncio.Lock] = {}
        self.concurrency_gate: asyncio.Semaphore | None = asyncio.Semaphore(3)
        self.sessions = SessionManager(workspace)
        self.session_ttl_minutes = session_ttl_minutes
        self.context_window_tokens = 65_536
        self.memory_store = MemoryStore(workspace)
        self.embed_store = MilvusMemoryStore(workspace)
        self.context_builder = ContextBuilder(workspace, embed_store=self.embed_store)
        self.consolidator = Consolidator(
            store=self.memory_store,
            embed_store=self.embed_store,
            provider=self.provider,
            model=self.model,
            sessions=self.sessions,
            get_tool_definitions=self.tools.get_definitions,
            context_window_tokens=self.context_window_tokens,
            build_messages=self.context_builder.build_messages,
        )
        self.dream = Dream(
            store=self.memory_store,
            provider=self.provider,
            model=self.model,
            max_batch_size=20,
            max_iterations=12,
        )
        self.compactor = Compactor(
            sessions=self.sessions,
            consolidator=self.consolidator,
            ttl_minutes=self.session_ttl_minutes,
        )

        self._mcp_servers: dict[str, MCPServerConfig] = dict(mcp_servers or {})
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False

    async def _connect_mcp(self) -> None:
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        try:
            from agent.mcp import connect_mcp_servers

            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
            else:
                logger.warning("No MCP servers connected (will retry on next message)")
        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry on next message)")
            self._mcp_stacks.clear()
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry on next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False

    async def close_mcp(self) -> None:
        for stack in self._mcp_stacks.values():
            try:
                await stack.aclose()
            except Exception:
                logger.debug("MCP stack cleanup error", exc_info=True)
        self._mcp_stacks.clear()
        self._mcp_connected = False

    def _register_default_tools(self, tools: ToolRegistry) -> None:
        """
        注册默认工具
        """
        tools.register(ReadFileTool())
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            tools.register(cls())
        tools.register(ShellTool(
            timeout=60,
            working_dir=str(self.workspace),
            restrict_to_workspace=True,
        ))
        tools.register(WebFetchTool())
        tools.register(CronTool(self.cron_service))

    
    def _schedule_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    async def _run_for_dispatch(self) -> None:
        """
        消息流程 1 : 接受输入，分发命令或消息
        """
        self._running = True
        input_task: asyncio.Task[str] | None = None

        while self._running:
            if input_task is None:
                input_task = asyncio.create_task(
                    asyncio.to_thread(input, "\033[36ms01 >> \033[0m")
                )
            
            done, _ = await asyncio.wait({input_task}, timeout=1.0)
            if not done:
                # 超时：执行后台任务，但继续等待同一个 input_task
                self.compactor.check_expired(
                    self._schedule_background,
                    active_session_keys=self.session_locks.keys(),
                )
                continue

            query = input_task.result().strip()
            input_task = None

            if self.commands.is_slash_command(query):
                await self._dispatch_command(query, self.commands.dispatch)
                continue
            await self._dispatch(query)

    async def _dispatch_command(self, query: str, dispatch_fn: Callable) -> None:
        """
        处理命令
        """
        result = await dispatch_fn(query)
        if result:
            print(result)
        else:
            print(f"Command '{query}' not found")

    async def _dispatch(self, query: str) -> None:
        """
        消息流程 2 : 设置并发锁
        """
        session_key = "cli:direct"
        lock = self.session_locks.setdefault(session_key, asyncio.Lock())
        gate = self.concurrency_gate or asyncio.Semaphore(1)
        try:
            async with lock, gate:
                response = await self._process_message(query, session_key)
                print(response if response else "No response")
        except Exception as e:
            print(f"Error processing message: {e}")

    async def _process_message(self, query: str, session_key: str) -> str | None:
        """
        消息流程 3 : 获取Session的历史记录, 得到初始消息
        """
        await self._connect_mcp()
        logger.info("MCP servers connected: {}", self._mcp_connected)
        preview = query[:80] + "..." if len(query) > 80 else query
        logger.info("Processing message from {}:{}: {}", "cli", "direct", preview)

        key = session_key
        session = self.sessions.get_or_create(key)
        
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session) 
        

        session, summary = self.compactor.prepare_session(session, key)
        # await self.consolidator.maybe_consolidate_by_tokens(
        #     session,
        #     session_summary=summary,  
        # )
        
        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            session_summary = summary,
        ) 

        self._set_tool_context(key)

        history = session.get_history(max_messages=0)
        logger.info("History: {}", json.dumps(history, ensure_ascii=False)[-500:])

        messages = self.context_builder.build_messages(
            history=history,
            current_messages=query,
            session_summary=summary,
            role="user",
        )
        final_content, all_msgs , _ = await self._run_agent_loop(
            messages,
            session=session,
        )

        self._save_turn(session, all_msgs, skip=len(history) + 1)
        self._clear_checkpoint(session)
        self.sessions.save(session)

        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))

        return final_content

    async def _run_agent_loop(
        self,
        messages: list[dict[str, Any]],
        session: Session,
    ) -> tuple[str, list[dict[str, Any]], str]:
        """
        消息流程 4 : 运行AgentRunner
        """
        # 设置cehckpoint回调，保存session消息，以便恢复中断的对话
        async def _checkpoint(payload: dict[str, Any]) -> None:
            """
            payload: dict[str, Any] = {
                "assistant_message": {
                    "role": "assistant",
                    "content": assistant_content,
                },
                "completed_tool_results": tool_results,
                "pending_tool_calls": tool_calls,
            }
            快照包括AI的响应，完成的工具调用结果，未完成的工具调用
            """
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        result = await self.runner.run(AgentRunSpec(
            messages=messages,
            tools=self.tools,
            model=self.model,
            workspace=self.workspace,
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            checkpoint_callback=_checkpoint,
        ))

        final_text = result.get("final_text")
        messages = result.get("messages")
        stop_reason = result.get("stop_reason")

        return final_text, messages, stop_reason

    def _save_turn(self, session: Session, messages: list[dict[str, Any]], skip: int) -> None:
        for message in messages[skip:]:
            role = message.get("role")
            if role not in {"user", "assistant", "tool"}:
                continue
            payload = {"content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in message:
                    payload[key] = message[key]
            session.add_message(role, payload["content"], **{k: v for k, v in payload.items() if k != "content"})

    def _build_messages(self, history: list[dict[str, Any]], current_message: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.context_block_limit > 0:
            history = history[-self.context_block_limit:]
        messages.extend(history)
        messages.append({"role": "user", "content": current_message})
        return messages

    def _set_tool_context(self, session_key: str) -> None:
        for name in ("message", "spawn", "cron", "my"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_session_key"):
                    tool.set_session_key(session_key)


    def _set_runtime_checkpoint(self, session: Session, payload: dict):
        session.metadata["runtime_checkpoint"] = payload
        self.sessions.save(session)
    
    def _clear_checkpoint(self, session: Session) -> None:
        if "runtime_checkpoint" in session.metadata:
            session.metadata.pop("runtime_checkpoint")

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        """Stable comparison for overlap dedup (ignore timestamp-only drift)."""
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
        )

    @staticmethod
    def _tool_use_id_from_pending_call(tool_call: dict[str, Any]) -> str:
        """Match runtime tool_calls shape: {id, name, input} or OpenAI {id, function:{name}}."""
        tid = tool_call.get("id") or tool_call.get("tool_call_id")
        if tid is not None:
            return str(tid)
        return ""

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        from datetime import datetime
        checkpoint = session.metadata.get("runtime_checkpoint")
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        ts = datetime.now().isoformat()

        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", ts)
            restored_messages.append(restored)

        # Align with AgentRunner.run: one user turn whose content is a list of
        # Anthropic-style tool_result blocks (not role="tool" OpenAI messages).
        tool_blocks: list[dict[str, Any]] = []
        for block in completed_tool_results:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id") or block.get("tool_call_id")
            if not tid:
                continue
            content = block.get("content")
            tool_blocks.append({
                "type": "tool_result",
                "tool_use_id": str(tid),
                "content": content if content is not None else "",
            })

        seen_ids = {b["tool_use_id"] for b in tool_blocks}
        pending_msg = "Error: Task interrupted before this tool finished."
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tid = self._tool_use_id_from_pending_call(tool_call)
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            tool_blocks.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": pending_msg,
            })

        if tool_blocks:
            restored_messages.append({
                "role": "user",
                "content": tool_blocks,
                "timestamp": ts,
            })

        if not restored_messages:
            self._clear_checkpoint(session)
            return False

        # Avoid duplicating tail if a prior save already materialized these messages.
        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        to_append = restored_messages[overlap:]
        if not to_append:
            self._clear_checkpoint(session)
            return True

        session.messages.extend(to_append)
        self._clear_checkpoint(session)
        return True


    async def process_direct(
        self, 
        content: str, 
        session_key: str = "cli:direct", 
    ):
        return await self._process_message(
            content,
            session_key,
        )

    # def _maybe_compact_session(self, session: Session) -> None:
    #     # Minimal local compaction: keep latest suffix and preserve a short summary.
    #     if len(session.messages) <= 80:
    #         return
    #     dropped = len(session.messages) - 60
    #     dropped_messages = session.messages[:dropped]
    #     summary_lines = []
    #     for msg in dropped_messages[-12:]:
    #         role = msg.get("role", "?")
    #         content = str(msg.get("content", "")).strip().replace("\n", " ")
    #         if content:
    #             summary_lines.append(f"{role}: {content[:120]}")
    #     if summary_lines:
    #         session.metadata["local_summary"] = "\n".join(summary_lines)
    #     session.messages = session.messages[dropped:]
    #     session.last_compact = 0


class AgentRunner:
    """
    单次处理消息的Agent运行器
    """
    def __init__(self, 
        provider: LLMProvider,
        ):
        self.provider = provider

    @staticmethod
    def _message_content_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        c = msg.get("content")
        if isinstance(c, list):
            return [b for b in c if isinstance(b, dict)]
        return []

    def _microcompact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Shrink model context: replace older large tool_result bodies with one-line placeholders.

        Matches persisted ``messages`` order (Anthropic: assistant ``tool_use`` ids map to
        following user ``tool_result`` blocks). Does not mutate the input list.
        """
        id_to_name: dict[str, str] = {}
        compactable_refs: list[tuple[int, int, str]] = []

        for msg_idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for block in self._message_content_blocks(msg):
                    if block.get("type") == "tool_use" and block.get("id"):
                        id_to_name[str(block["id"])] = str(block.get("name", "") or "")
            elif role == "user":
                raw_content = msg.get("content")
                if not isinstance(raw_content, list):
                    continue
                for bi, block in enumerate(raw_content):
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tid = block.get("tool_use_id")
                    if not tid:
                        continue
                    name = id_to_name.get(str(tid), "")
                    if name not in _COMPACTABLE_TOOLS:
                        continue
                    raw = block.get("content")
                    if not isinstance(raw, str) or len(raw) < _MICROCOMPACT_MIN_CHARS:
                        continue
                    compactable_refs.append((msg_idx, bi, name))

        if len(compactable_refs) <= _MICROCOMPACT_KEEP_RECENT:
            return messages

        stale = compactable_refs[: len(compactable_refs) - _MICROCOMPACT_KEEP_RECENT]
        out = copy.deepcopy(messages)
        for msg_idx, bi, name in stale:
            try:
                content = out[msg_idx].get("content")
                if not isinstance(content, list):
                    continue
                block = content[bi]
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                block["content"] = f"[{name} result omitted from context]"
            except (IndexError, KeyError, TypeError):
                continue
        return out

    async def run(self, spec: AgentRunSpec) -> dict[str, Any]:
        messages = list(spec.messages)
        final_text: str | None = None
        error: str | None = None
        tool_events: list[dict[str, str]] = []
        stop_reason: str = "completed"
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        length_recovery_count = 0

        for iteration in range(spec.max_iterations):
            messages_for_model = self._microcompact(messages)
            response = await self._request_model(spec, messages_for_model)
           
            assistant_content = response.content if response.content is not None else ""
            tool_calls = response.tool_calls
            raw_usage = response.usage
            self._accumulate_usage(usage, raw_usage)
            
            logger.info("Response: {}", assistant_content[:50])
            logger.info("Tool calls: {}", tool_calls[:50])

            #如果有工具调用
            if  response.should_execute_tools:
                content_blocks: list[dict[str, Any]] = []
                if assistant_content:
                    content_blocks.append({"type": "text", "text": assistant_content})
                for call in tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": call.get("name", ""),
                        "input": call.get("input", {}) if isinstance(call.get("input"), dict) else {},
                    })
                assistant_message: dict[str, Any] = {"role": "assistant", "content": content_blocks}
                
                await self._emit_checkpoint(spec.checkpoint_callback, payload={
                    "phase": "awaiting_tools",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": assistant_message,
                    "completed_tool_results": [],
                    "pending_tool_calls": tool_calls,
                })

                messages.append(assistant_message)

                tool_results, new_events, fatal_error = await self._execute_tools(
                    spec,
                    tool_calls = tool_calls,
                )
                tool_events.extend(new_events)

                tool_result_blocks: list[dict[str, Any]] = []
                for tool_call, tool_results in zip(tool_calls, tool_results):
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": tool_results,
                    })
                if tool_result_blocks:
                    logger.info(f"Tool results({len(tool_result_blocks)}): {tool_result_blocks[0]['content'][:50]}")
                    messages.append({"role": "user", "content": tool_result_blocks})
                if fatal_error is not None:
                    logger.error("Fatal error: {}", fatal_error)
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_text = error
                    messages.append({"role": "assistant", "content": final_text})
                    # Persist checkpoint matching in-memory state (user tool_result row
                    # was already appended); disk must not stay on awaiting_tools alone.
                    await self._emit_checkpoint(spec.checkpoint_callback, payload={
                        "phase": "tool_error",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": tool_result_blocks,
                        "pending_tool_calls": [],
                    })
                    break

                await self._emit_checkpoint(spec.checkpoint_callback, payload={
                    "phase": "tools_completed",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": assistant_message,
                    "completed_tool_results": tool_result_blocks,
                    "pending_tool_calls": [],
                })
                continue

            else: #没有工具调用
                #如果Token超出限制，则重试
                if response.finish_reason == "length":
                    length_recovery_count += 1
                    if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                        logger.info("Output truncated on turn {} for {} ({}/{}); continuing", iteration, spec.model, length_recovery_count, _MAX_LENGTH_RECOVERIES)
                        assistant_message = {"role": "assistant", "content": assistant_content}
                        messages.append(assistant_message)
                        messages.append(build_length_recovery_message())
                        continue
                    else:
                        logger.error("Output truncated on turn {} for {} ({}/{}); stopping", iteration, spec.model, length_recovery_count, _MAX_LENGTH_RECOVERIES)
                        error = f"Error: Output truncated on turn {iteration} for {spec.model} ({length_recovery_count}/{_MAX_LENGTH_RECOVERIES})"
                        final_text = error
                        messages.append({"role": "assistant", "content": final_text})
                        break
    
                assistant_message = {"role": "assistant", "content": assistant_content}
                messages.append(assistant_message)
                await self._emit_checkpoint(spec.checkpoint_callback, payload={
                    "phase": "final_response",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": assistant_message,
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                })

                final_text = assistant_content
                break

        else:
            stop_reason = "max_iterations"
            messages.append({
                "role": "assistant",
                "content": "I reached the maximum number of tool call iterations ({}) without completing the task. You can try breaking the task into smaller steps.".format(spec.max_iterations),
            })

        return {
            "final_text": final_text,
            "messages": messages,
            "stop_reason": stop_reason,
            "usage": usage,
            "tool_events": tool_events,
            "error": error,
        }

    # def _drop_orphan_tool_results(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    #     declared: set[str] = set()
    #     updated: list[dict[str, Any]] | None = None

    #     for idx, msg in enumerate(messages):
    #         role = msg.get("role")
    #         if role == ""



    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    )->LLMResponse:
        kwargs = self._build_request_kwargs(spec, messages, tools=spec.tools.get_definitions())
        return await self.provider.chat(**kwargs)

    def _build_request_kwargs(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "model": spec.model,
        }
        if spec.temperature is not None:
            kwargs["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens
        return kwargs
    
    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 根据可并行性进行分批
        batches = self._partition_tool_batches(spec, tool_calls)
        
        tool_results: list[tuple[Any, BaseException | None]] = []
        for batch in batches:
            if len(batch) > 1:
                tool_results.extend(await asyncio.gather(
                    *(self._run_tool(spec, tool_call) for tool_call in batch)
                ))
            else:
                tool_results.append(await self._run_tool(spec, batch[0]))

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error



    async def _run_tool(self, spec: AgentRunSpec, tool_call: dict[str, Any]) -> tuple[Any, BaseException | None]:
        prepare_before_call = getattr(spec.tools, "prepare_before_call", None)
        tool, params, error = None, None, None
        if callable(prepare_before_call):
            try:
                tool, params, error = prepare_before_call(tool_call["name"], tool_call["input"])
            except Exception as e:
                pass
        if error:
            event = {
                "name": tool_call["name"],
                "status": "error",
                "detail": error.split(": ", 1)[-1][:120],
            }
            return error, event, RuntimeError(error)

        try:
            if tool is not None:
                result = await tool.run(**params)
            else:
                result = await spec.tools.execute(tool_call["name"], tool_call["input"])        
        except BaseException as e:
            event = {
                "name": tool_call["name"],
                "status": "error",
                "detail": str(e),
            }
            return f"Error: {type(e).__name__}: {e}", event, e

        tool_result = "(empty)" if result is None else str(result)
        return tool_result, {"name": tool_call["name"], "status": "ok", "detail": tool_result[:120] + "..." if len(tool_result) > 120 else tool_result}, None

    def _accumulate_usage(self, usage: dict[str, int], raw_usage: dict[str, int]) -> None:
        if raw_usage is None:
            return
        for key, value in raw_usage.items():
            usage[key] = usage.get(key, 0) + value

    
    def _partition_tool_batches(
        self,
        spec: AgentRunSpec,
        tool_calls: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        # if not spec.concurrent_tools:
        #     return [[tool_call] for tool_call in tool_calls]

        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            get_tool = getattr(spec.tools, "get", None)
            tool = get_tool(tool_call["name"]) if callable(get_tool) else None
            can_batch = bool(tool and tool.concurrency_safe)
            if can_batch:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches

    async def _emit_checkpoint(
        self,
        checkpoint_callback: Callable[[dict[str, Any]], None],
        payload: dict[str, Any],
    ) -> None:
        if checkpoint_callback is not None:
            await checkpoint_callback(payload)


