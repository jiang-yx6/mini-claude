from datetime import datetime, timedelta, timezone
from typing import Any

from cron.service import CronService
from tools.base import tool_parameters
from tools.base import Tool
from cron.types import CronSchedule, CronJobState, CronJob
from contextvars import ContextVar

# 中国东八区（北京/上海同一标准时），不依赖 tzdata
LOCAL_TZ = timezone(timedelta(hours=8))
LOCAL_TZ_NAME = "Asia/Shanghai"


cron_parameters = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "list", "remove"]},
        "name": {"type": "string", "description": "Optional short human-readable label for the job (e.g., 'weather-monitor', 'daily-standup'). Defaults to first 30 chars of message."},
        "message": {"type": "string", "description": "REQUIRED when action='add'. Instruction for the agent to execute when the job triggers (e.g., 'Send a reminder to WeChat: xxx' or 'Check system status and report'). Not used for action='list' or action='remove'."},
        "at": {"type": "string", "description": "ISO datetime for one-time execution. Naive values use China time (UTC+8)."},
        "cron_expr": {"type": "string", "description": "Cron expression like '0 9 * * *' (for scheduled tasks)"},
        "tz": {"type": "string", "description": "Optional IANA timezone for cron_expr; if omitted, Asia/Shanghai."},
        "deliver": {"type": "boolean", "description": "Whether to deliver the execution result to the user channel (default true)"},
        "job_id": {"type": "string", "description": "REQUIRED when action='remove'. Job ID to remove (obtain via action='list')."},
    },
    "required": ["action"],
}


@tool_parameters(schema=cron_parameters)
class CronTool(Tool):
    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._session_key: ContextVar[str] = ContextVar("cron_session_key", default="")
        self._in_cron_context: ContextVar[bool] = ContextVar("in_cron_context", default=False)

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule reminders and recurring tasks. Actions: add, list, remove. "
            "Default wall clock: China UTC+8 (Asia/Shanghai). "
            "Unless the user asks for recurring schedules, prefer a single one-shot cron with `at`."
        )

    def set_session_key(self, session_key: str) -> None:
        self._session_key.set(session_key)

    def set_cron_context(self, active: bool):
        return self._in_cron_context.set(active)

    def reset_cron_context(self, token) -> None:
        self._in_cron_context.reset(token)

    async def run(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        deliver: bool = True,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(name, message, every_seconds, cron_expr, tz, at, deliver)
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        deliver: bool = True,
    ) -> str:
        if not message:
            return (
                "Error: cron action='add' requires a non-empty 'message' parameter describing what to do when the job triggers "
                '(e.g. the reminder text). Retry including message="...".'
            )

        session_key = self._session_key.get()
        if not session_key:
            return "Error: no session context (session_key)"

        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            if err := self._validate_timezone(tz):
                return err

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz or LOCAL_TZ_NAME)
        elif at:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime format '{at}'. Expected format: YYYY-MM-DDTHH:MM:SS"
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_TZ)
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=name or message[:30],
            schedule=schedule,
            message=message,
            deliver=deliver,
            session_key=session_key,
            delete_after_run=delete_after,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for j in jobs:
            timing = self._format_timing(j.schedule)
            parts = [f"- {j.name} (id: {j.id}, {timing})"]
            if j.payload.kind == "system_event":
                parts.append(f"  Purpose: {self._system_job_purpose(j)}")
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        result = self._cron.remove_job(job_id)
        if result == "removed":
            return f"Removed job {job_id}"
        if result == "protected":
            if job_id == "dream":
                return (
                    "Cannot remove job `dream`.\n"
                    "This is a system-managed Dream memory consolidation job.\n"
                    "It stays registered; adjust the schedule in code if needed."
                )
            return (
                f"Cannot remove job `{job_id}`.\n"
                "This is a protected system-managed cron job."
            )
        return f"Job {job_id} not found"

    def _validate_timezone(self, tz: str) -> str | None:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(tz)
        except (KeyError, Exception):
            return f"Error: unknown timezone '{tz}'"
        return None

    def _format_timing(self, schedule: CronSchedule) -> str:
        if schedule.kind == "cron":
            tzx = f" ({schedule.tz})" if schedule.tz else ""
            return f"cron: {schedule.expr}{tzx}"
        if schedule.kind == "every" and schedule.every_ms:
            ms = schedule.every_ms
            if ms % 3_600_000 == 0:
                return f"every {ms // 3_600_000}h"
            if ms % 60_000 == 0:
                return f"every {ms // 60_000}m"
            if ms % 1000 == 0:
                return f"every {ms // 1000}s"
            return f"every {ms}ms"
        if schedule.kind == "at" and schedule.at_ms:
            return f"at {self._format_ts(schedule.at_ms)}"
        return schedule.kind

    def _format_state(self, state: CronJobState, schedule: CronSchedule) -> list[str]:
        lines: list[str] = []
        if state.last_run_at_ms:
            info = f"  Last run: {self._format_ts(state.last_run_at_ms)} — {state.last_status or 'unknown'}"
            if state.last_error:
                info += f" ({state.last_error})"
            lines.append(info)
        if state.next_run_at_ms:
            lines.append(f"  Next run: {self._format_ts(state.next_run_at_ms)}")
        return lines

    @staticmethod
    def _format_ts(ms: int) -> str:
        dt = datetime.fromtimestamp(ms / 1000, tz=LOCAL_TZ)
        return f"{dt.isoformat()} ({LOCAL_TZ_NAME})"

    @staticmethod
    def _system_job_purpose(job: CronJob) -> str:
        if job.name == "dream":
            return "Dream memory consolidation for long-term memory."
        return "System-managed internal job."
