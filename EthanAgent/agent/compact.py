from datetime import datetime
from typing import  Callable, Coroutine
from collections.abc import Collection
from session.manager import SessionManager
from agent.memory import Consolidator


class Compactor:
    def __init__(
        self, 
        sessions: SessionManager, 
        consolidator: Consolidator, 
        ttl_minutes: int = 30
    ):
        self.ttl_minutes = ttl_minutes
        self.sessions = sessions
        self._archiving: set[str] = set()
        self._summaries: dict[str, tuple[str, datetime]] = {}
        self.consolidator = consolidator

    def _is_expired(self, ts: datetime | str | None,
        now : datetime | None = None) -> bool:
        """
        是否超时
        ts: 会话最后活跃时间
        now: 当前时间
        """
        if self.ttl_minutes <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self.ttl_minutes * 60

 
    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                        active_session_keys: Collection[str] = ()) -> None:
        now = datetime.now()
        for session_id, session in self.sessions.items():
            if not session_id or session_id in self._archiving:
                continue
            if session_id in active_session_keys:
                continue
            if self._is_expired(session.get("updated_at"), now):
                self._archiving.add(session_id)
                schedule_background(self._archive(session_id))
                
    
    async def _archive(self, session_id: str) -> None:
        try: 
            self.sessions.invalidate(session_id)
            session = self.sessions.get_or_create(session_id)
            archive_msgs, kept_msgs = self._split_unconsolidated(session)
            if not archive_msgs and not kept_msgs:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return 
            last_active = session.updated_at
            summary = ""

            if archive_msgs:
                summary = await self.consolidator.archive(archive_msgs) or ""
            if summary and summary != "(nothing)":
                self._summaries[session_id] = (summary, last_active)
                session.metadata["_last_summary"] = {"text": summary, "last_active": last_active.isoformat()}
            session.messages = kept_msgs
            session.last_compact = 0
            session.updated_at = datetime.now()
            self.sessions.save(session)
            
        except Exception as e:
            print(f"Failed to archive session {session_id}: {e}")
    