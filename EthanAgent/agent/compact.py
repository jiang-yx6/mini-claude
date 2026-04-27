from datetime import datetime
from typing import  Callable, Coroutine, Any
from collections.abc import Collection
from session.manager import SessionManager,Session
from agent.memory import Consolidator
from loguru import logger
class Compactor:
    _RECENT_SUFFIX_MESSAGES = 8
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
        """
        检查所有Session是否超时,如果超时,则归档
        schedule_background: 调度后台任务
        active_session_keys: 活跃的Session列表
        """
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
        """
        执行归档操作，真正更新Session的消息
        session_id: Session的key
        """
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
            if archive_msgs:
                logger.info(
                    "Auto-compact: archived {} (archived={}, kept={}, summary={})",
                    session_id,
                    len(archive_msgs),
                    len(kept_msgs),
                    bool(summary),
                )
        except Exception:
            logger.exception("Auto-compact: failed for {}", session_id)
        finally:
            self._archiving.discard(session_id)

    def _split_unconsolidated(self, session:Session) -> tuple[list[dict[str,Any]], list[dict[str,Any]]]:
        """
        把session的所有消息分成 归档消息 和 最近保留消息
        不改变原Session的消息，只做分割计算
        """
        tail = list(session.messages[session.last_compact:])
        if not tail:
            return [], []
        probe = Session(
            key = session.key,
            messages = tail.copy(),
            created_at=session.created_at,
            updated_at=session.updated_at,
            metadata={},
            last_compact=0,
        )
        # 保留最近的消息，以前的消息直接删除
        probe.keep_recent_legal_suffix(self._RECENT_SUFFIX_MESSAGES)
        kept = probe.messages
        cut = len(tail) - len(kept)
        return tail[:cut], kept

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        
        entry = self._summaries.pop(key, None)
        if entry:
            session.metadata.pop("_last_summary", None)
            return session,self._format_summary(entry[0], entry[1])
        if "_last_summary" in session.metadata:
            meta = session.metadata.pop("_last_summary")
            self.sessions.save(session)
            return session,self._format_summary(meta["text"], datetime.fromisoformat(meta["last_active"]))
        return session, None


    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        idle_min = int((datetime.now() - last_active).total_seconds() / 60)
        return f"Inactive for {idle_min} minutes.\nPrevious conversation summary: {text}"
