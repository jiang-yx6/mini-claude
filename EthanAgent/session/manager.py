from dataclasses import dataclass,field
from typing import Any
from datetime import datetime
from pathlib import Path
import json

@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]]
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
            "timestamp" : datetime.now().isoformat()
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages:int = 100) -> list[dict[str, Any]]:
        unconsolidated = self.messages[self.last_compact:]
        sliced = unconsolidated[-max_messages:]

        # 从用户消息开始
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break
        
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
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.sessions: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        if key in self.sessions:
            return self.sessions[key]
        
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self.sessions[key] = session
        return session

    def _load(self, key: str) -> Session:
        path = self.sessions_dir / f"{key}.jsonl"
        if not path.exists():
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
        path = self.sessions_dir / f"{session.key}.jsonl"

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