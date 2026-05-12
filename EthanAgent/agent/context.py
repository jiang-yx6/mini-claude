from pathlib import Path
from agent.memory import MemoryStore, MilvusMemoryStore
from agent.skills import SkillsLoader
import platform
from datetime import datetime
from utils.templates import render_template
from typing import Any

from loguru import logger


def _describe_local_timezone() -> str:
    """Host local zone for prompts: IANA key when available, else tzname + offset."""
    now = datetime.now().astimezone()
    tz = now.tzinfo
    if tz is None:
        return "unknown"
    iana = getattr(tz, "key", None)
    label = iana or tz.tzname(now) or type(tz).__name__
    return f"{label} (UTC{now.strftime('%z')}), 当前时间 {now.strftime('%Y-%m-%d %H:%M:%S')}"


class ContextBuilder:
    def __init__(
        self,
        workspace: Path,
        disabled_skills: set[str] | None = None,
        *,
        embed_store: MilvusMemoryStore | None = None,
    ):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.embed_store = embed_store
        self.skills = SkillsLoader(workspace, disabled_skills=disabled_skills)

    def build_system_prompt(
        self,
    ):
        parts = []
        parts.append(self._get_identity())
        memory_ctx = self.memory.get_memory_context()
        if memory_ctx:
            parts.append(f"# 长期记忆\n\n{memory_ctx}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(render_template("skills_section.md", skills_summary=skills_summary))

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) ->str:
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "identity.md",
            workspace_path=workspace_path,
            platform_policy=render_template("platform_policy.md", system=system),
            runtime=runtime,
            local_timezone=_describe_local_timezone(),
        )

    def _semantic_recall_block(self, query: str) -> str:
        es = self.embed_store
        if not es or not es.is_ready:
            logger.info("no embed store or embed store is not ready")
            return ""
        q = (query or "").strip()
        if not q or q == "[token-probe]":
            return ""
        try:
            raw = es.search_memory(q)
            logger.info("length of semantic recall raw: {}", len(raw))
        except Exception:
            logger.exception("semantic recall search failed")
            return ""
        if not raw or not raw[0]:
            return ""
        parts: list[str] = []
        for i, hit in enumerate(raw[0], start=1):
            if not isinstance(hit, dict):
                continue
            ent = hit.get("entity", hit)
            if not isinstance(ent, dict):
                continue
            ts = ent.get("timestamp", "?")
            content = str(ent.get("content", "") or "").strip()
            if not content:
                continue
            parts.append(f"### {i} ({ts})\n{content}")
        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_messages: str,
        session_summary: str | None = None,
        role: str = "user",
        *,
        vector_memory: bool = True,
    ):
        """
        构造输入给LLM的messages:
         - system: system_identity + memory [+ 向量召回，仅当 vector_memory 为 True]
         - history: past few messages
         - user: runtime_context + current_messages

        ``vector_memory=False`` 用于 token 探测等内部路径，避免每次估算都访问 Milvus。
        """
        system_content = self.build_system_prompt()
        if vector_memory:
            recall = self._semantic_recall_block(current_messages)
            if recall:
                system_content = f"{system_content}\n\n---\n\n# 相关历史记忆\n\n{recall}"

        messages = [
            {"role": "system", "content": system_content},
            *history,
        ]

        merged = f"{self._build_runtime_context(session_summary)}\n\n{current_messages}"

        messages.append({"role": role, "content": merged})

        return messages

    def _build_runtime_context(self, session_summary: str | None = None) -> str:
        if session_summary:
            return f"[Resumed Session]\n{session_summary}\n[/Resumed Session]"
        return ""
