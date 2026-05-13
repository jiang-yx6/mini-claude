from dataclasses import dataclass,field
from typing import Any
from datetime import datetime
from pathlib import Path
import json
import re
from loguru import logger


def _content_is_only_tool_results(content: Any) -> bool:
    """Anthropic: user turn after tool_use is a list of tool_result blocks only."""
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _tool_ids_from_assistant_message(msg: dict[str, Any]) -> set[str]:
    """OpenAI tool_calls and/or Anthropic content blocks with type tool_use."""
    ids: set[str] = set()
    if msg.get("role") != "assistant":
        return ids
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("id"):
            ids.add(str(tc["id"]))
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                bid = block.get("id")
                if bid:
                    ids.add(str(bid))
    return ids


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Drop illegal prefix: tool results must follow a matching assistant tool declaration.

    Aligns with nanobot ``find_legal_message_start``; extends Ethan Anthropic
    ``user`` rows whose ``content`` is a list of ``tool_result`` blocks.
    """
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            declared |= _tool_ids_from_assistant_message(msg)
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
        elif role == "user" and _content_is_only_tool_results(msg.get("content")):
            needed: list[str] = []
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tuid = block.get("tool_use_id")
                    if tuid:
                        needed.append(str(tuid))
            if needed and any(tid not in declared for tid in needed):
                start = i + 1
                declared.clear()
    return start


@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_compact: int = 0 # 已经压缩的消息数量
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """
        Session中增加消息
        """
        msg = {
            "role" : role,
            "content" :content,
            "timestamp" : datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages:int = 100) -> list[dict[str, Any]]:
        unconsolidated = list(self.messages[self.last_compact:])
        if max_messages and max_messages > 0:
            sliced = unconsolidated[-max_messages:]
        else:
            sliced = unconsolidated

        # 从第一条 user 开始（与 nanobot 类似：避免从半截开始）
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry : dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        self.messages = []
        self.last_compact = 0
        self.updated_at = datetime.now()
    
    def keep_recent_legal_suffix(self, max_messages:int) -> None:
        """
        只保留max_messages条消息
        """
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return
        
        start_idx = max(0, len(self.messages) - max_messages)

        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1
        
        retained = self.messages[start_idx:]
        trim = find_legal_message_start(retained)
        if trim:
            retained = retained[trim:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_compact = max(0, self.last_compact - dropped)
        self.updated_at = datetime.now()


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path

class SessionManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / ".sessions")
        self.sessions: dict[str, Session] = {}

    @staticmethod
    def _safe_key_filename(key: str) -> str:
        """
        Map a session key (e.g. 'cli:direct') to a filesystem-safe filename stem.
        Windows forbids characters like ':' in filenames.
        """
        if not isinstance(key, str) or not key:
            return "default"
        # Replace common separators and illegal filename characters.
        safe = key.replace(":", "_").replace("/", "_").replace("\\", "_")
        safe = re.sub(r'[<>:"/\\\\|?*]+', "_", safe)
        safe = safe.strip(" ._")
        return safe or "default"

    def _session_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self._safe_key_filename(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        if key in self.sessions:
            return self.sessions[key]
        
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self.sessions[key] = session
        return session

    def _load(self, key: str) -> Session:
        path = self._session_path(key)
        if not path.exists():
            logger.warning(f"Session file not found: {path}")
            return None        
        try:
            messages = []
            created_at = None
            updated_at = None
            last_compact = 0
            metadata = {}
            
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_compact = data.get("last_compact", 0)
                        metadata = data.get("metadata", {})
                    else:
                        messages.append(data)
                
            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                last_compact=last_compact,
                metadata=metadata,
            )

        except Exception as e:
            print(f"Failed to load session {key}: {e}")
            return None
        

    
    def save(self, session: Session) -> None:
        # 保存session
        path = self._session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "last_compact": session.last_compact,
                "metadata": session.metadata,
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self.sessions[session.key] = session


    def invalidate(self, key: str) -> None:
        self.sessions.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for path in self.sessions_dir.glob("*.jsonl"):
            key = path.stem.replace("_", ":", 1)
            with open(path, encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    data = json.loads(first_line)
                    if data.get("_type") == "metadata":
                        sessions.append({
                            "key": key,
                            "created_at": data.get("created_at"),
                            "updated_at": data.get("updated_at"),
                            "path": str(path)
                        })

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)