# Consolidator 和 Dream 都依赖 MemoryStore
# 典型链路是：
# 会话运行时：Consolidator 把旧对话沉淀到 history.jsonl
# 后台周期：Dream 再把这些历史提炼进长期记忆文件
from dataclasses import field
import importlib
import os
import sys
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
import tiktoken
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
**保证足够简洁精炼，避免冗长**
如果没有值得注意的内容，输出：(nothing)
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
    """memory/MEMORY.md, SOUL.md, USER.md, history.jsonl, .dream_cursor"""

    _MAX_HISTORY_LINES = 2000
    _MEMORY_CONTEXT_MAX_PER_FILE = 16_000

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = Path(workspace / "memory")
        self.history_file = self.memory_dir / "history.jsonl"
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.soul_file = self.memory_dir / "SOUL.md"
        self.user_file = self.memory_dir / "USER.md"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"

    @staticmethod
    def read_text_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_memory(self) -> str:
        return self.read_text_file(self.memory_file)

    def read_soul(self) -> str:
        return self.read_text_file(self.soul_file)

    def read_user(self) -> str:
        return self.read_text_file(self.user_file)

    def get_memory_context(self) -> str:
        """Build text for system prompt: SOUL / USER / MEMORY under ``memory/``.

        Empty or whitespace-only files are omitted. Each file is capped to
        ``_MEMORY_CONTEXT_MAX_PER_FILE`` chars so prompts stay bounded.
        """
        limit = self._MEMORY_CONTEXT_MAX_PER_FILE

        def cap(text: str) -> str:
            t = text.strip()
            if not t:
                return ""
            if len(t) <= limit:
                return t
            return t[: limit - 24].rstrip() + "\n... (truncated)"

        parts: list[str] = []
        s_soul = cap(self.read_soul())
        s_user = cap(self.read_user())
        s_mem = cap(self.read_memory())
        if s_soul:
            parts.append(f"## memory/SOUL.md（助手人设与风格）\n\n{s_soul}")
        if s_user:
            parts.append(f"## memory/USER.md（用户长期信息）\n\n{s_user}")
        if s_mem:
            parts.append(f"## memory/MEMORY.md（项目与长期事实）\n\n{s_mem}")
        return "\n\n".join(parts)

    def get_last_dream_cursor(self) -> int:
        """Last processed physical line index in ``history.jsonl`` (1-based); 0 means none yet.

        Dream only reads lines *after* this index. Line numbers are recomputed from the file
        on each run (not stored inside each JSON object).
        """
        try:
            return max(0, int(self._dream_cursor_file.read_text(encoding="utf-8").strip()))
        except (ValueError, OSError):
            return 0

    def set_last_dream_cursor(self, line_index: int) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._dream_cursor_file.write_text(str(line_index), encoding="utf-8")

    def read_unprocessed_history(self, since_line: int) -> list[dict[str, Any]]:
        """JSON objects from ``history.jsonl`` on lines strictly after *since_line* (1-based).

        Each item includes ``cursor`` = that line number for advancing ``.dream_cursor``.
        """
        out: list[dict[str, Any]] = []
        if not self.history_file.exists():
            return out
        with open(self.history_file, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line_no <= since_line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                out.append({
                    "cursor": line_no,
                    "timestamp": str(obj.get("timestamp", "")),
                    "content": str(obj.get("content", "")),
                })
        return out

    def compact_history(self) -> None:
        """Trim oldest lines if ``history.jsonl`` exceeds ``_MAX_HISTORY_LINES``.

        After truncation, **dream cursor is reset to 0** so the next Dream pass re-evaluates
        the retained tail (safe if old lines were dropped; avoids a stale cursor past EOF).
        No-op if under the cap.
        """
        if not self.history_file.exists():
            return
        try:
            lines = [ln for ln in self.history_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return
        if len(lines) <= self._MAX_HISTORY_LINES:
            return
        kept = lines[-self._MAX_HISTORY_LINES :]
        self.history_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
        self.set_last_dream_cursor(0)
        logger.warning(
            "history.jsonl trimmed to last {} lines; dream cursor reset to 0",
            self._MAX_HISTORY_LINES,
        )

    def append_history(self, entry: str, max_chars: int | None = None) -> dict[str, str]:
        limit = max_chars if max_chars is not None else _ARCHIVE_SUMMARY_MAX_CHARS
        entry = entry[:limit] + "\n... (truncated)" if len(entry) > limit else entry
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": ts, "content": entry}
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

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

from pymilvus import MilvusClient, DataType, Function, FunctionType

_VARCHAR_MAX = 16_000
_PK_FIELD = "my_id"


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class MilvusMemoryStore:
    """Vector memory: writes go to DB ``db_name``; reads use the same scoped client (no default-db mismatch)."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.db_name = os.environ.get("ETHAN_MILVUS_DB", "ethanagent").strip() or "ethanagent"
        self.collection_name = os.environ.get("ETHAN_MILVUS_COLLECTION", "memory").strip() or "memory"
        self.top_k = max(1, int(os.environ.get("ETHAN_MILVUS_TOP_K", "3")))
        cred = os.environ.get("ETHAN_MILVUS_CREDENTIAL", "apikey1").strip() or "apikey1"
        self._enabled = _env_bool("ETHAN_MILVUS_ENABLED", True)
        self.Function = Function(
            name="vector_search",
            description="Search for similar memories",
            function_type=FunctionType.TEXTEMBEDDING,
            input_field_names=["content"],
            output_field_names=["my_vector"],
            params={
                "provider": "dashscope",
                "model_name": "text-embedding-v3",
                "dim": 1024,
                "credential": cred,
            },
        )
        self.milvus_client: MilvusClient | None = None
        if self._enabled:
            self.milvus_client = self._init_client()
            self.milvus_client.load_collection(self.collection_name, replica_number=1)

    @property
    def is_ready(self) -> bool:
        return self.milvus_client is not None

    def _init_client(self) -> MilvusClient | None:
        uri = os.environ.get("ETHAN_MILVUS_URI", "http://localhost:19530").strip() or "http://localhost:19530"
        timeout = float(os.environ.get("ETHAN_MILVUS_TIMEOUT", "10"))
        try:
            bootstrap = MilvusClient(uri=uri, timeout=timeout)
        except Exception:
            logger.exception("Milvus bootstrap connect failed")
            return None
        try:
            databases = bootstrap.list_databases()
            if self.db_name not in databases:
                try:
                    bootstrap.create_database(
                        db_name=self.db_name,
                    )
                except Exception:
                    bootstrap.create_database(db_name=self.db_name)
        except Exception:
            logger.exception("Milvus ensure database %s failed", self.db_name)
            return None
        try:
            client = MilvusClient(uri=uri, db_name=self.db_name, timeout=timeout)
            logger.info("Milvus connected uri={} db={}", uri, self.db_name)
        except Exception:
            logger.exception("Milvus connect to db %s failed", self.db_name)
            return None

        try:
            collections = client.list_collections()
            if self.collection_name not in collections:
                schema = MilvusClient.create_schema(enable_dynamic_field=True)
                schema.add_field(
                    field_name="my_id",
                    datatype=DataType.INT64,
                    is_primary=True,
                    auto_id=True,
                )
                schema.add_field(field_name="my_vector", datatype=DataType.FLOAT_VECTOR, dim=1024)
                schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=_VARCHAR_MAX)
                schema.add_field(field_name="timestamp", datatype=DataType.VARCHAR, max_length=64)
                schema.add_function(self.Function)
                index_params = client.prepare_index_params()
                index_params.add_index(
                    field_name="my_vector",
                    index_type="AUTOINDEX",
                    metric_type="COSINE",
                )
                client.create_collection(
                    collection_name=self.collection_name,
                    schema=schema,
                    index_params=index_params,
                )
        except Exception:
            logger.exception("Milvus ensure collection %s failed", self.collection_name)
            return None

        return client

    def add_memory(self, history: list[dict[str, Any]]) -> None:
        if not self.milvus_client or not history:
            return
        rows: list[dict[str, Any]] = []
        for row in history:
            content = str(row.get("content", "") or "")
            if len(content) > _VARCHAR_MAX:
                content = content[: _VARCHAR_MAX - 20] + "\n... (truncated)"
            ts = str(row.get("timestamp", "") or "")[:64]
            rows.append({"content": content, "timestamp": ts})
        try:
            res = self.milvus_client.insert(collection_name=self.collection_name, data=rows)
            logger.debug("Milvus insert_count={}", res.get("insert_count"))
        except Exception:
            logger.exception("Milvus add_memory failed")

    def search_memory(self, query: str) -> list[list[dict[str, Any]]]:
        if not self.milvus_client:
            return []
        q = (query or "").strip()
        if not q:
            return []
        try:
            raw = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[q[:4000]],
                limit=self.top_k,
                output_fields=["content", "timestamp", _PK_FIELD],
                search_params={"metric_type": "COSINE", "params": {
                    "radius": 0.7,
                }},
                anns_field="my_vector",
            )
        except Exception:
            logger.exception("Milvus search_memory failed")
            return []

        return raw


class Consolidator:
    _SAFETY_BUFFER = 1024
    _MAX_CONSOLIDATION_ROUNDS = 3
    def __init__(
        self,
        store: MemoryStore,
        embed_store: MilvusMemoryStore | None,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        context_window_tokens: int,
        max_completion_tokens: int = 4096,
    ):
        self.store = store
        self.embed_store = embed_store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self._build_messages = build_messages
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self.get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        return self._locks.setdefault(session_key, asyncio.Lock())
   
    async def archive(self, messages: list[dict]) -> str | None:
        """
        将消息通过LLM提炼成摘要，并保存到history.jsonl
        """
        if not messages:
            return None
        try:
            formatted = format_messages(messages)
            # formatted = self._truncate_to_token_budget(formatted)
            response = await self.provider.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": CONSOLIDATOR_ARCHIVE_PROMPT,
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            row = self.store.append_history(summary, max_chars=_ARCHIVE_SUMMARY_MAX_CHARS)
            if self.embed_store and self.embed_store.is_ready:
                self.embed_store.add_memory([row])
            return summary
        except Exception:
            logger.exception("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages)
            return None

    async def maybe_consolidate_by_tokens(
        self,
        session: Session,
        session_summary: str | None = None
    ):
        """
        
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            input_token_budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER 
            budget = input_token_budget // 2
            try:
                estimated, source = self.estimate_session_prompt_tokens(
                    session,
                    session_summary=session_summary,
                )
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            if estimated <= 0:
                return
            if estimated < budget:
                unconsolidated_count = len(session.messages) - session.last_compact
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    unconsolidated_count,
                )
                return
            
            last_summary = None
            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= budget:
                    break

                boundary = self.pick_consolidation_boundary(
                    session,
                    max(1, estimated - budget),
                )
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                end_idx = boundary[0]
                chunk = session.messages[session.last_compact:end_idx]
                if not chunk:
                    break
                
                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )

                summary = await self.archive(chunk)
                if summary:
                    last_summary = summary
                session.last_compact = end_idx
                self.sessions.save(session)
                if not summary:
                    break
                

                try:
                    estimated, source = self.estimate_session_prompt_tokens(
                        session,
                        session_summary=session_summary,
                    )
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    break
            if last_summary and last_summary != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": last_summary,
                    "last_active": session.updated_at.isoformat(),
                }
                self.sessions.save(session)


    def estimate_session_prompt_tokens(
        self,
        session:Session,
        session_summary: str | None = None,
    )-> tuple[int, str]:
        history = session.get_history(max_messages=0)
        probe_messages = self._build_messages(
            history=history,
            current_messages="[token-probe]",
            session_summary=session_summary,
            vector_memory=False,
        )
        tokens = self._flatten_messages_content_calculate_tiktoken(
            messages=probe_messages,
            tools=self.get_tool_definitions(),
        )
        if tokens > 0:
            return tokens, "tiktoken"
        return 0, "none"

    @staticmethod
    def _flatten_messages_content_calculate_tiktoken(
        messages: Any,
        tools: list[dict[str, Any]],
        *,
        include_tools: bool = True,
    ) -> int:
        parts: list[str] = []
        if isinstance(messages, dict):
            messages = [messages]

        for msg in messages:
            if not isinstance(msg, dict):
                parts.append(str(msg))
                continue
            content = msg.get("content")
            if content is None:
                continue
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type")
                        if block_type == "text":
                            parts.append(str(block.get("text", "")))
                        elif block_type == "tool_result":
                            parts.append(str(block.get("content", "")))
                        elif block_type == "tool_use":
                            parts.append(json.dumps(block.get("input", {}), ensure_ascii=False))
                        else:
                            parts.append(json.dumps(block, ensure_ascii=False))
                    else:
                        parts.append(str(block))
            elif isinstance(content, dict):
                parts.append(json.dumps(content, ensure_ascii=False))

        if include_tools and tools:
            parts.append(json.dumps(tools, ensure_ascii=False))
        try: 
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode("\n".join(parts))) + 4
        except Exception:
            return 0        

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ):
        start = session.last_compact
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None
        removed_tokens = 0
        last_boundary = None

        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    # 达到压缩大小要求，返回边界
                    return last_boundary
            removed_tokens += self._flatten_messages_content_calculate_tiktoken(
                message,
                self.get_tool_definitions(),
                include_tools=False,
            )
        return last_boundary

def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n... (truncated)"


# Paths relative to workspace (Ethan keeps SOUL/USER under memory/)
_DREAM_PHASE1_SYSTEM = """You consolidate episodic history into long-term memory files.

You have TWO tasks:
1) Extract new facts from the conversation history batch.
2) Deduplicate: flag redundant/overlapping content across memory files even if not in history.

Output one line per finding:
- [memory/USER.md] atomic fact (identity, preferences)
- [memory/SOUL.md] bot tone/behavior rule
- [memory/MEMORY.md] durable project/context fact
- [memory/<path>-REMOVE] reason to delete a redundant line or section

Rules:
- Atomic facts only; skip transient errors, filler, weather.
- Prefer canonical file: USER identity, SOUL persona, MEMORY project facts — avoid duplication across files.
- If nothing needs updating, output a single line: [SKIP]

Workspace layout (relative paths only):
- memory/SOUL.md — bot behavior and tone
- memory/USER.md — user identity and preferences
- memory/MEMORY.md — project facts and durable context
"""


_DREAM_PHASE2_SYSTEM = """You apply the Phase-1 analysis by editing files with tools.

## Allowed paths (relative to workspace root)
- memory/SOUL.md
- memory/USER.md
- memory/MEMORY.md

## Rules
- Use read_file when you need the exact current file body; use edit_file for surgical replacements (unique old_text).
- Do not rewrite entire memory files unless the analysis explicitly requires a full restructure (prefer incremental edits).
- If Phase-1 was [SKIP] or there is nothing to change, respond with a short text and do not call tools.
- Dedup: remove or shorten redundant bullets per the analysis [memory/...-REMOVE] lines.
"""


class Dream:
    """Cron-driven two-phase memory: analyze history.jsonl, then run a short tool loop (nanobot-style, simplified)."""

    _PREVIEW_MEMORY = 24_000
    _PREVIEW_SOUL = 12_000
    _PREVIEW_USER = 12_000
    _PREVIEW_HISTORY_ENTRY = 4_000

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 12,
        max_tool_result_chars: int = 16_000,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars

    def _build_dream_tools(self):
        from tools.file import EditFileTool, ReadFileTool
        from tools.tool_registry import ToolRegistry

        reg = ToolRegistry()
        reg.register(ReadFileTool())
        reg.register(EditFileTool())
        return reg

    def _ensure_memory_files(self) -> None:
        self.store.memory_dir.mkdir(parents=True, exist_ok=True)
        for p in (self.store.memory_file, self.store.soul_file, self.store.user_file):
            if not p.exists():
                p.write_text("", encoding="utf-8")

    def _runner_classes(self) -> tuple[type, type]:
        """Resolve AgentRunSpec / AgentRunner whether we run as ``python agent_runner.py`` or as a library."""
        main = sys.modules.get("__main__")
        if main and hasattr(main, "AgentRunSpec") and hasattr(main, "AgentRunner"):
            return main.AgentRunSpec, main.AgentRunner
        ar = importlib.import_module("agent_runner")
        return ar.AgentRunSpec, ar.AgentRunner

    async def run(self) -> bool:
        """Process new history lines since last dream run. Returns True if a batch was processed."""
        AgentRunSpec, AgentRunner = self._runner_classes()

        self._ensure_memory_files()
        last = self.store.get_last_dream_cursor()
        entries = self.store.read_unprocessed_history(last)
        if not entries:
            logger.debug("Dream: no new history lines after cursor {}", last)
            return False

        batch = entries[: self.max_batch_size]
        logger.info(
            "Dream: batch {} lines (cursor {} → {})",
            len(batch),
            last,
            batch[-1]["cursor"],
        )

        history_text = "\n".join(
            f"[{e['timestamp']}] {_truncate(e['content'], self._PREVIEW_HISTORY_ENTRY)}"
            for e in batch
        )
        current_date = datetime.now().strftime("%Y-%m-%d")
        mem = _truncate(self.store.read_memory(), self._PREVIEW_MEMORY) or "(empty)"
        soul = _truncate(self.store.read_soul(), self._PREVIEW_SOUL) or "(empty)"
        user = _truncate(self.store.read_user(), self._PREVIEW_USER) or "(empty)"
        file_ctx = (
            f"## Current date\n{current_date}\n\n"
            f"## memory/MEMORY.md\n{mem}\n\n"
            f"## memory/SOUL.md\n{soul}\n\n"
            f"## memory/USER.md\n{user}"
        )
        phase1_user = f"## History batch\n{history_text}\n\n{file_ctx}"

        try:
            r1 = await self.provider.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": _DREAM_PHASE1_SYSTEM},
                    {"role": "user", "content": phase1_user},
                ],
                tools=None,
                max_tokens=8192,
                temperature=0.2,
            )
            if r1.finish_reason == "error":
                logger.error("Dream phase1 provider error: {}", r1.content)
                return False
            analysis = (r1.content or "").strip()
        except Exception:
            logger.exception("Dream phase1 failed")
            return False

        # Advance cursor on [SKIP] so we do not re-run Phase 1 on the same batch.
        first_line = (analysis.splitlines()[0].strip().upper() if analysis else "")
        if not analysis or first_line == "[SKIP]" or first_line.startswith("[SKIP]"):
            self.store.set_last_dream_cursor(batch[-1]["cursor"])
            self.store.compact_history()
            logger.info("Dream: skipped (no analysis); cursor → {}", batch[-1]["cursor"])
            return True

        phase2_user = f"## Analysis\n{analysis}\n\n{file_ctx}"
        runner = AgentRunner(self.provider)
        tools = self._build_dream_tools()
        cwd_before = os.getcwd()
        try:
            os.chdir(self.store.workspace)
            result = await runner.run(
                AgentRunSpec(
                    messages=[
                        {"role": "system", "content": _DREAM_PHASE2_SYSTEM},
                        {"role": "user", "content": phase2_user},
                    ],
                    tools=tools,
                    model=self.model,
                    workspace=self.store.workspace,
                    max_iterations=self.max_iterations,
                    max_tokens=8192,
                    temperature=0.1,
                    checkpoint_callback=None,
                )
            )
        except Exception:
            logger.exception("Dream phase2 failed")
            result = None
        finally:
            try:
                os.chdir(cwd_before)
            except OSError:
                pass

        new_cursor = batch[-1]["cursor"]
        self.store.set_last_dream_cursor(new_cursor)
        self.store.compact_history()

        if result:
            logger.info(
                "Dream: phase2 stop_reason={} tool_events={}",
                result.get("stop_reason"),
                len(result.get("tool_events") or []),
            )
        return True





# if __name__ == "__main__":
#     memory_store = MilvusMemoryStore(Path("."))
#     memory_store.add_memory([
#         {"timestamp": "2026-05-05 20:17", "content": "- 用户名字为“轩轩”，自称“轩轩大王”，是男生\n- 用户要求取消了每15分钟的学习提醒任务（cron job id: 1d6b77b4），因不再需要\n- 用户对英语口语学习感兴趣但觉得难，目前不愿用英语对话\n- 用户设置了30秒后的喝水提醒（cron job id: 69f0d03b）"},
#         {"timestamp": "2026-05-05 22:13", "content": "- 用户名字为“轩轩”，自称“轩轩大王”，是男生"},
#         {"timestamp": "2026-05-05 22:14", "content": "- 用户要求对EthanAgent项目进行全面的代码审查（Code Review）。\n- 项目路径：`D:\\ProgramFiles\\JetBrains\\PyCharmProjects\\mini-claude\\EthanAgent`\n- 用户偏好：中文沟通。\n- 用户偏好：使用“检查一下你的agent代码”作为起始命令，然后要求“帮我做一个Code_Review”，表明用户希望得到结构化的、全面的代码质量分析。\n- 用户偏好：使用`read_file`工具来获取代码内容。\n- 用户偏好：使用`list_dir`工具来了解项目结构。\n- 用户偏好：用户对代码审查的期望非常高，希望覆盖安全性、正确性、性能、可维护性和可观测性等多个维度。\n- 决策：用户决定让AI助手进行全面的代码审查。\n- 事件：在2026-05-05T22:03，用户要求检查agent代码。\n- 事件：在2026-05-05T22:14，用户再次要求进行Code_Review。\n- 解决方案：在代码审查中，发现了`SessionManager.list_sessions`中key重建逻辑的bug（`replace(\"_\", \":\", 1)`），这是一个非显而易见的解决方案，需要修复。\n- 解决方案：在代码审查中，发现了`providers/base.py`中未使用的import（`from ast import UAdd`、`from tkinter import ANCHOR`、`from anthropic.types import content_block`），需要清理。\n- 用户事实：用户希望代码审查能够发现并指出关键问题，并给出修复建议。\n- 用户事实：用户对AI助手的能力有较高期望，希望它能进行深入的分析。"},
#         {"timestamp": "2026-05-06 20:21", "content": "好的，根据 **Code Review Skill** 的规范，我对 EthanAgent 项目的 **代码结构** 进行了审查。下面是我的发现。\n\n---\n\n## Code Review：EthanAgent 代码结构\n\n### 摘要\n项目整体模块划分清晰，职责边界明确，是一个典型的 Agent 框架架构。但仍存在一些模块划分上的**小瑕疵**，以及部分文件**缺少 `__init__.py`** 导致包结构不完整。\n\n---\n\n### 严重问题\n\n1. **代理模块 (`agent/`) 缺少 `__init__.py`**\n   - 位置：`agent/` 目录\n   - 说明：`agent/` 目录下有 `context.py`、`memory.py`、`compact.py`、`skills.py`，但**没有 `__init__.py`**。\n   - 影响：Python 3.3+ 虽支持命名空间包，但缺少 `__init__.py` 会导致：\n     - IDE 自动补全不可靠\n     - `from agent import ...` 可能失败\n     - 工具如 `mypy` / `pylint` 可能报错\n   - 建议：创建 `agent/__init__.py`，可留空或导出关键类。\n\n2. **`commands/`、`cron/`、`providers/`、`session/`、`tools/`、`utils/` 全部缺少 `__init__.py`**\n   - 影响同上。整个项目除了根目录外，**没有任何一个子包有 `__init__.py`**。\n   - 建议：为每个子包创建 `__init__.py`。\n\n---\n\n### 改进建议\n\n1. **`agent_runner.py` 过于庞大，建议拆分**\n   - 当前约 800 行，包含了：\n     - `EthanAgentLoop`（主循环）\n     - `AgentRunner`（单次 Agent 执行）\n     - `AgentRunSpec`（数据类）\n     - `register_dream_system_job()`（工具函数）\n     - `attach_cron_job_handler()`（工具函数）\n   - 建议：将 `AgentRunner` 和 `AgentRunSpec` 移到 `agent/runner.py`，将两个工具函数移到 `cron/handler.py` 或 `agent/cron_jobs.py`。\n\n2. **`agent/skills.py` 命名不清晰**\n   - 位置：`agent/skills.py`\n   - 说明：从之前的 code review 看，`skills.py` 内容是 Skill 加载逻辑，但文件名 `skills` 过于泛化。\n   - 建议：改名为 `skill_loader.py` 或直接整合到 `agent/__init__.py`。\n\n3. **`utils/` 目录职责模糊**\n   - 当前 `utils/` 下只有：\n     - `runtime.py`（token 恢复消息）\n     - `templates.py`（Jinja2 模板渲染）\n   - 这两个功能差异较大，建议按职责拆分或更名：\n     - `utils/runtime.py` → `utils/recovery.py`\n     - `utils/templates.py` → `utils/prompt_templates.py` 或直接移到 `agent/templates.py`\n\n4. **`commands/commands.py` 命名冗余**\n   - `commands/commands.py` 包含 `register_commands()` 和 `CommandRouter`。\n   - 建议：将 `CommandRouter` 保留在 `commands/router.py`，`register_commands()` 放到 `commands/__init__.py`。\n\n---\n\n### 亮点\n\n- ✅ **模块边界清晰**：`session/`、`cron/`、`providers/`、`tools/` 职责单一，易于理解和维护。\n- ✅ **工具注册模式优雅**：`ToolRegistry` + `@tool_parameters` 装饰器，扩展新工具只需新建类并注册。\n- ✅ **Cron 服务设计良好**：`CronService` 与 `CronTool` 分离，`system_event` 保护机制防止误删系统任务。\n- ✅ **Session 持久化设计合理**：`SessionManager` + JSONL 存储，支持 checkpoint 恢复。\n\n---\n\n### 结论\n\n- [ ] 可直接合入\n- [x] 需小改\n- [ ] 需大改\n\n**主要改动建议：**\n1. 为所有子包添加 `__init__.py`（**高优先级**）\n2. 拆分 `agent_runner.py`，将 `AgentRunner` 移到 `agent/runner.py`（**建议**）\n3. 清理 `commands/commands.py` 的命名冗余（**低优先级**）\n\n需要我帮你执行这些结构优化吗？"},
#         {"timestamp": "2026-05-06 21:02", "content": "好的，我明白了。刚才的搜索尝试都失败了，主要是两个原因：\n\n1. **网络/反爬问题** — 搜索引擎（Bing、DuckDuckGo）返回的是带大量 JavaScript 的页面，纯文本解析提取不到有效结果\n2. **工具限制** — 我的 shell 工具在 Windows 下编码处理有问题，中文输出乱码\n\n**我没办法直接帮你实时搜索招聘网站。**\n\n不过我可以给你一些建议，告诉你去哪里查：\n\n---\n\n## 上海 Agent 开发实习 — 去哪里找\n\n### 推荐渠道\n\n| 平台 | 搜索关键词 | 说明 |\n|------|-----------|------|\n| **BOSS直聘** | `AI Agent` `Agent开发` `大模型应用` | 上海岗位最多，实习岗常见 |\n| **实习僧** | `AI Agent` `大模型` `LLM` | 专注实习 |\n| **拉勾** | `Agent` `AI应用` | 互联网公司为主 |\n| **牛客网** | 讨论区 + 内推 | 很多内推帖 |\n| **小红书** | `上海 Agent 实习` `大模型 实习` | 很多面经和内推 |\n\n### 推荐搜索关键词组合\n\n```\n上海 AI Agent 开发 实习\n上海 大模型应用 实习\n上海 LLM Agent 实习\n上海 LangChain 实习\n上海 智能体 开发 实习\n```\n\n### 哪些公司可能招\n\n- **大厂**：字节（上海）、阿里（上海）、腾讯（上海）、美团、百度\n- **AI 创业公司**：月之暗面（Kimi）、MiniMax、智谱 AI（上海有 office）、百川智能\n- **外企**：Microsoft（上海）、Google（上海）— Agent 相关岗位多\n\n---\n\n要我帮你做点别的吗？比如继续修项目的 bug，或者整理一份 Agent 开发实习需要的技能清单？"},
#         {"timestamp": "2026-05-07 11:40", "content": "- 用户询问上海中小厂5月份Agent开发日常实习的招聘情况\n- 360搜索结果显示2026年5月上海有Agent开发日常实习岗位，涉及中小厂（如AI垂类Agent初创公司、阶跃星辰等）\n- 牛客网有2026年3月发布的上海Agent开发实习内推帖，面向2026.9-2027.8毕业的学生，可留用"},
#     ])
#     memory_store.search_memory("上海Agent开发日常实习的招聘情况") 