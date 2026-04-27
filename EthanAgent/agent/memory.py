# Consolidator 和 Dream 都依赖 MemoryStore
# 典型链路是：
# 会话运行时：Consolidator 把旧对话沉淀到 history.jsonl
# 后台周期：Dream 再把这些历史提炼进长期记忆文件
from pathlib import Path
from typing import Any, Callable
from session.manager import SessionManager
import weakref
import asyncio
from session.manager import Session
from loguru import logger
from datetime import datetime
import json
from providers.base import LLMProvider
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000

CONSOLIDATOR_ARCHIVE_PROMPT = """从本次对话中提取关键事实。仅输出符合以下类别的内容，跳过其他所有内容：
用户事实：个人信息、偏好、陈述的观点、习惯
决策：做出的选择、达成的结论
解决方案：通过反复试错发现的可行方法，特别是那些在失败尝试后成功的非显而易见的方法
事件：计划、截止日期、值得注意的事件
偏好：沟通风格、工具偏好
优先级：用户的更正和偏好 > 解决方案 > 决策 > 事件 > 环境事实。最有价值的记忆是避免用户必须重复自己的内容。
跳过：可从源代码推导出的代码模式、Git 历史记录或任何已包含在现有记忆中的内容。
输出为简洁的要点，每行一个事实。不要前言，不要评论。
如果没有值得注意的内容，输出：(无)
"""

    
def format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

class MemoryStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = Path(workspace / "memory")
        self.history_file = self.memory_dir / "history.jsonl"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.soul_file = self.memory_dir / "SOUL.md"
        self.user_file = self.memory_dir / "USER.md"

    def append_history(self, entry: str, max_chars: int | None = None) -> int:
        limit = max_chars if max_chars is not None else _ARCHIVE_SUMMARY_MAX_CHARS
        entry = entry[:limit] + "\n... (truncated)" if len(entry) > limit else entry
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": ts, "content": entry}, ensure_ascii=False) + "\n")

    def raw_archive(self, messages: list[dict], *, max_chars: int | None = None) -> None:
        limit = max_chars if max_chars is not None else _ARCHIVE_SUMMARY_MAX_CHARS
        formatted = format_messages(messages)
        formatted = formatted[:limit] + "\n... (truncated)" if len(formatted) > limit else formatted
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{formatted}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )

class Consolidator:
    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        # build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        context_window_tokens: int = 4096,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        # self.build_messages = build_messages 
        self.get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())

    # def estimate_session_tokens(
    #     self,
    #     session: Session,
    #     session_summary: str | None = None,
    # ) -> tuple[int, str]:
    #     history = session.get_history(max_messages=0)

    #     probe_messages = self.build_messages(
    #         history=history,
    #         current_message="[token-probe]",
    #         session_summary=session_summary,
    #     )

   
    async def archive(self, messages: list[dict]) -> str | None:
        """
        将消息通过LLM提炼成摘要，并保存到history.jsonl
        """
        if not messages:
            return None
        try:
            formatted = format_messages(messages)
            # formatted = self._truncate_to_token_budget(formatted)
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": CONSOLIDATOR_ARCHIVE_PROMPT,
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            self.store.append_history(summary, max_chars=_ARCHIVE_SUMMARY_MAX_CHARS)
            return summary
        except Exception:
            logger.exception("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages)
            return None

    # async def maybe_consolidate_by_tokens(
    #     self,
    #     session: Session,
    #     session_summary: str | None = None
    # ):
    #     if not session.messages or self.context_window_tokens <= 0:
    #         return

    #     lock = self.get_lock(session.key)
    #     async with lock:
    #         budget = self.context_window_tokens

class Dream:
    pass