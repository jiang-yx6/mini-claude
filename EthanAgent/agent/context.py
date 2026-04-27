from pathlib import Path
from agent.memory import MemoryStore
import platform
from utils.templates import render_template
from typing import Any
class ContextBuilder:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)

    def build_system_prompt(
        self,
    ):
        parts = []
        parts.append(self._get_identity())
        # memory: str | None = self.memory.get_memory_context()
        # if memory:
        #     parts.append(f"# Memory\n\n{memory}")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) ->str:
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "identity.md",
            workspace_path=workspace_path, 
            platform_policy=render_template("platform_policy.md", system=system),
            runtime=runtime
        )

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_messages: str,
        session_summary: str | None = None,
        role: str = "user",
    ):   
        """
        构造输入给LLM的messages:
         - system: system_identity + memory
         - history: past few messages
         - user: runtime_context + current_messages
        """         
        messages = [
            {"role": "system", "content": self.build_system_prompt()},
            *history,
        ]

        merged = f"{self._build_runtime_context(session_summary)}\n\n{current_messages}"
        messages.append({"role": role, "content": merged})
        
        return messages

    def _build_runtime_context(self, session_summary: str | None = None) -> str:
        if session_summary:
            return f"[Resumed Session]\n{session_summary}\n[/Resumed Session]"
        return ""
