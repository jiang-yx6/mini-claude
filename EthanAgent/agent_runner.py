from unittest import result
from pydantic import BaseModel, ConfigDict
from tools.base import Tool
from pathlib import Path
import asyncio 

from tools.tool_registry import ToolRegistry
from typing import Any, Callable
from pathlib import Path
from agent.context import ContextBuilder
from agent.memory import Consolidator, MemoryStore
from agent.compact import Compactor

from tools.file import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from tools.shell import ShellTool
from tools.tool_registry import ToolRegistry
from commands.router import CommandRouter
from commands.commands import register_commands
from session.manager import SessionManager, Session
from providers.base import LLMProvider, AnthropicProvider, LLMResponse
from uuid import uuid4
from loguru import logger
import os
import dotenv

dotenv.load_dotenv()

class AgentRunSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    workspace: Path
    max_iterations: int
    max_tokens: int | None = None
    temperature: float | None = None    


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
        
    ):
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations or 10
        self.max_tokens = None
        self.temperature = None
        self.context_block_limit = context_block_limit or 40
        self.max_tool_result_chars = max_tool_result_chars or 4000

        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self._register_default_tools(self.tools)
        self.commands = CommandRouter()
        register_commands(self.commands)

        self.session_locks: dict[str, asyncio.Lock] = {}
        self.concurrency_gate: asyncio.Semaphore | None = asyncio.Semaphore(3)
        self.sessions = SessionManager(workspace)
        self.session_ttl_minutes = session_ttl_minutes

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

    async def _run_for_dispatch(self) -> None:
        """
        消息流程 1 : 接受输入，分发命令或消息
        """
        self._running = True
        while self._running:
            query = input("\033[36ms01 >> \033[0m")
            if not query.strip():
                continue
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
        preview = query[:80] + "..." if len(query) > 80 else query
        logger.info("Processing message from {}:{}: {}", "cli", "direct", preview)

        key = session_key
        session = self.sessions.get_or_create(key)
        self._maybe_compact_session(session)
        history = session.get_history(max_messages=0)
        initial_messages = self._build_messages(history=history, current_message=query)

        final_content, all_msgs = await self._run_agent_loop(
            initial_messages,
            session=session,
        )
        self._save_turn(session, all_msgs, skip=len(history))
        return final_content

    async def _run_agent_loop(
        self,
        messages: list[dict[str, Any]],
        session: Session,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        消息流程 4 : 运行AgentRunner
        """
        result = await self.runner.run(AgentRunSpec(
            messages=messages,
            tools=self.tools,
            model=self.model,
            workspace=self.workspace,
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        ))
        return result

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
        self.sessions.save(session)

    def _build_messages(self, history: list[dict[str, Any]], current_message: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.context_block_limit > 0:
            history = history[-self.context_block_limit:]
        messages.extend(history)
        messages.append({"role": "user", "content": current_message})
        return messages

    def _maybe_compact_session(self, session: Session) -> None:
        # Minimal local compaction: keep latest suffix and preserve a short summary.
        if len(session.messages) <= 80:
            return
        dropped = len(session.messages) - 60
        dropped_messages = session.messages[:dropped]
        summary_lines = []
        for msg in dropped_messages[-12:]:
            role = msg.get("role", "?")
            content = str(msg.get("content", "")).strip().replace("\n", " ")
            if content:
                summary_lines.append(f"{role}: {content[:120]}")
        if summary_lines:
            session.metadata["local_summary"] = "\n".join(summary_lines)
        session.messages = session.messages[dropped:]
        session.last_compact = 0


class AgentRunner:
    """
    单次处理消息的Agent运行器
    """
    def __init__(self, 
        provider: LLMProvider,
        ):
        self.provider = provider

    async def run(self, spec: AgentRunSpec) -> tuple[str, list[dict[str, Any]]]:
        messages = list(spec.messages)
        final_text = ""

        for _ in range(spec.max_iterations):
            try:
                response = await self._request_model(spec, messages)
            except Exception as e:
                return f"Error requesting model: {e}", messages

            assistant_content, tool_calls = self._parse_response(response)
            assistant_message: dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)

            if not tool_calls:
                final_text = assistant_content.strip() or "(empty response)"
                return final_text, messages

            for call in tool_calls:
                tool_name = call["name"]
                tool_input = call["input"]
                tool_call_id = call["id"]
                result = await spec.tools.execute(tool_name, tool_input)
                tool_result = str(result)
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": tool_result,
                            }
                        ],
                    }
                )

        return f"Reached max_iterations={spec.max_iterations} without final answer.", messages

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    )->LLMResponse:
        kwargs =  self._build_request_kwargs(spec, messages, tools=spec.tools.get_definitions())
        return await self.provider.chat(**kwargs)

    def _parse_response(self, response: Any) -> tuple[str, list[dict[str, Any]]]:
        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content, []

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}) or {},
                    }
                )
        return "\n".join([x for x in text_parts if x]).strip(), tool_calls

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




if __name__ == "__main__":
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    api_base = os.getenv("ANTHROPIC_API_BASE", "https://api.deepseek.com/anthropic")
    model = os.getenv("MODEL_NAME", "deepseek-chat")
    workspace = Path(__file__).resolve().parent

    loop = EthanAgentLoop(
        provider=AnthropicProvider(api_base=api_base, api_key=api_key),
        workspace=workspace,
        model=model,
        max_iterations=10,
        max_tool_result_chars=4000,
        context_block_limit=30,
        session_ttl_minutes=30,
    )
    asyncio.run(loop._run_for_dispatch())