from typing import Any, Callable, Coroutine, Literal
from pathlib import Path
import asyncio
from filelock import FileLock
from cron.types import CronJob,CronSchedule,CronStore,CronPayload,CronJobState,CronRunRecord
from datetime import datetime
import json
from loguru import logger
import time
from dataclasses import asdict
import uuid


def compute_next_turn(schedule: CronSchedule, now_ms:int) -> int | None:
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None
    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        return now_ms + schedule.every_ms
    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo
            from croniter import croniter

            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None
    return None

def validate_schedule_for_add(schedule: CronSchedule) -> None:
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")
    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


        

class CronService:

    _MAX_RUN_HISTORY = 10

    def __init__(self, 
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        self.store_path = store_path
        self._action_path = store_path.parent / "action.jsonl"
        self._lock = FileLock(str(self._action_path.parent) + ".lock")
        self.on_job = on_job
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task | None = None
        self._running = False
        self._timer_active = False
        self.max_sleep_ms = max_sleep_ms    


    
    def _load_jobs(self) -> tuple[list[CronJob], int]:
        """从文件中加载jobs"""
        jobs = []
        version = 1
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                version = data.get("version", 1)

                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id = j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            session_key=j["payload"].get("session_key"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                            run_history=[
                                CronRunRecord(
                                    run_at_ms=r["runAtMs"],
                                    status=r["status"],
                                    duration_ms=r.get("durationMs", 0),
                                    error=r.get("error"),
                                )
                                for r in j.get("state", {}).get("runHistory", [])
                            ],
                        ),  
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
        return jobs, version


    def _merge_action(self):
        """
        将action文件中的操作合并到store中
        """
        if not self._action_path.exists():
            return
        
        jobs_map = {j.id: j for j in self._store.jobs}
        def _update(params: dict):
            j = CronJob.from_dict(params)
            jobs_map[j.id] = j

        def _del(params:dict):
            if job_id := params.get("job_id"):
                jobs_map.pop(job_id)
            
        with self._lock:
            with open(self._action_path, "r", encoding="utf-8") as f:
                changed = False
                for line in f:
                    try: 
                        line = line.strip()
                        action = json.loads(line)
                        if "action" not in action:
                            continue
                        if action["action"] == "del":
                            _del(action.get("params", {}))
                        else:
                            _update(action.get("params", {}))
                        changed = True
                    except Exception as e:
                        logger.debug(f"load action line error: {e}")
                        continue
            self._store.jobs = list(jobs_map.values())
            if self._running and changed:
                self._action_path.write_text("", encoding="utf-8")
                self._save_store()
        return 
    

    def _load_store(self) -> CronStore:
        if self._timer_active and self._store:
            return self._store
        jobs, version = self._load_jobs()
        self._store = CronStore(version=version, jobs=jobs)
        self._merge_action()

        return self._store
  
    def _save_store(self) -> None:
        """
        存盘
        """
        if not self._store:
            return
        
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "session_key": j.payload.session_key,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,  
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                } for j in self._store.jobs
            ]
        }

        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        self._load_store()  # 加载jobs
        self._recompute_next_runs() #重新计算jobs的触发时间
        self._save_store() #存盘
        self._arm_timer() #启动定时器
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = int(time.time() * 1000)
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = compute_next_turn(job.schedule, now)

    
    def _arm_timer(self) -> None:
        """
        启动定时器，到期触发on_timer回调
        """
        if self._timer_task:
            self._timer_task.cancel()
        if not self._running:
            return 

        # 找到最早触发的时间
        next_wake = self._get_next_wake_time_ms()
        if next_wake is None:
            delay_ms = self.max_sleep_ms
        else:
            delay_ms = min(self.max_sleep_ms, max(0, next_wake - int(time.time() * 1000)))
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    def _get_next_wake_time_ms(self) -> int | None:
        """找到最早触发的时间"""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None
    
    async def _on_timer(self) -> None:
        """处理定时器到期事件"""
        self._load_store()
        if not self._store:
            self._arm_timer()
            return
        
        self._timer_active = True
        try: 
            now = int(time.time() * 1000)
            due_jobs = [
                j for j in self._store.jobs
                if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
            ]

            for job in due_jobs:
                await self._execute_job(job)
            
            self._save_store()

        finally:
            self._timer_active = False
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        start_ms = int(time.time() * 1000)
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)
        try:
            if self.on_job:
                await self.on_job(job)
            
            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)
        
        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        end_ms = int(time.time() * 1000)
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        job.state.run_history.append(CronRunRecord(
            run_at_ms=start_ms,
            status=job.state.last_status,
            duration_ms=end_ms - start_ms,
            error=job.state.last_error,
        ))
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY:]

        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
            
        else:
            job.state.next_run_at_ms = compute_next_turn(job.schedule, end_ms)
        
    def _append_action(self, action: Literal["add", "del", "update"], params: dict):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._action_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "params": params}, ensure_ascii=False) + "\n")

# ========== Public API ==========


    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key = lambda j: j.state.next_run_at_ms or float('inf'))

    
    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        session_key: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """增加一个job"""
        validate_schedule_for_add(schedule)

        now = int(time.time() * 1000)
        job = CronJob(
            id = str(uuid.uuid4())[:8],
            name = name,
            enabled = True,
            schedule = schedule,
            payload = CronPayload(
                kind = "agent_turn",
                message = message,
                session_key=session_key,
                deliver=deliver,
            ),
            state = CronJobState(next_run_at_ms=compute_next_turn(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        if self._running:
            store = self._load_store()
            store.jobs.append(job)
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("add", asdict(job))

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def remove_job(self, job_id: str) -> Literal["removed", "protected", "not_found"]:
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            logger.info("Cron: refused to remove protected system job {}", job_id)
            return "protected"
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before
        if removed:
            if self._running:
                self._save_store()
                self._arm_timer()
            else:
                self._append_action("del", {"job_id": job_id})
            logger.info("Cron: removed job {}", job_id)
            return "removed"
        return "not_found"


    
    def register_system_job(self, job: CronJob) -> CronJob:
        """注册一个系统job"""
        store = self._load_store()
        now = int(time.time() * 1000)
        job.state = CronJobState(next_run_at_ms=compute_next_turn(job.schedule, now))
        job.created_at_ms = now
        job.updated_at_ms = now
        store.jobs = [j for j in store.jobs if j.id != job.id]
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        logger.info("Cron: registered system job '{}' ({})", job.name, job.id)
        return job

    
    async def run_job(self, job_id: str, force: bool = False) -> bool:
        was_running = self._running
        self._running = True
        try:
            store = self._load_store()
            for job in store.jobs:
                if job.id == job_id:
                    if not force and not job.enabled:
                        return False
                    await self._execute_job(job)
                    self._save_store()
                    return True
            
            return False
        finally:
            self._running = was_running
            if was_running:
                self._arm_timer()

