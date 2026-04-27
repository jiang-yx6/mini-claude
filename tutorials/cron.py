import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timedelta
from queue import Queue, Empty
from settings import WORKDIR
from pathlib import Path
import time 

SCHEDULED_TASKS_FILE = WORKDIR / ".claude" / "scheduled_tasks.json"
CRON_LOCK_FILE = WORKDIR / ".claude" / "cron.lock"
AUTO_EXPIRY_DAYS = 7
JITTER_MINUTES = [0, 30]  # avoid these exact minutes for recurring tasks
JITTER_OFFSET_MAX = 4     # offset range in minutes

class CronLock:
    def __init__(self, lock_path: Path = None):
        self._lock_path = lock_path or CRON_LOCK_FILE

    def acquire(self) -> bool:
        """
        Try to acquire the cron lock. Returns True on success
        If a lock file exists, check whether the PID inside is still alive.
        If the process is dead the lock is stale and we can take over.
        """
        if self._lock_path.exists():
            try:
                stored_pid = int(self._lock_path.read_text().strip())
                #探测进程是否死了，没死不会报错
                os.kill(stored_pid, 0)
                return False

            #进程已经死了
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(str(os.getpid()))
        return True
    
    def release(self):
        """Remove the lock file if it belongs to this process"""
        try:
            if self._lock_path.exists():
                stored_pid = int(self._lock_path.read_text().strip())
                if stored_pid == os.getpid():
                    self._lock_path.unlink()
        except (ValueError, OSError):
            pass


def cron_matches(expr: str, dt: datetime) -> bool:
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    
    values = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
    # Python weekday: 0=Monday; cron: 1=Monday. Convert.
    cron_dow = (dt.weekday() + 1) % 7
    values[4] = cron_dow
    ranges = [(0,59) , (0,23), (1,31), (1,12), (0,6)]
    for field , value, (lo, hi) in zip(fields, values, ranges):
        if not _field_matches(field, value, lo, hi):
            return False
    return True

def _field_matches(field: str, value:int, lo:int, hi:int) -> bool:
    """Match a single cron field against a value"""
    if field == "*":
        return True
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)
        if part == "*":
            # */N
            if (value - lo) % step == 0:
                return True
        elif "-" in part:
            # N-M / S
            start, end = part.split("-" , 1)
            start, end = int(start), int(end)
            if start <= value <= end and (value - start) % step == 0:
                return True
        else:
            if int(part) == value:
                return True
    
    return False



class CronScheduler:
    """
    Manage scheduled tasks with background checking
    """
    def __init__(self):
        self.tasks = []
        self.queue = Queue()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_check_minute = -1

    def start(self):
        self._load_durable()
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        count = len(self.tasks)
        if count:
            print(f"[Cron] Loaded {count} scheduled tasks")
        
    
    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def create(self, cron_expr: str, prompt: str,
               recurring:bool =True, durable: bool = False) -> str:
        """Create a new shceduled task. Returns the task ID."""
        task_id = str(uuid.uuid4())[:8]
        now = time.time()
        task = {
            "id": task_id,
            "cron": cron_expr,
            "prompt": prompt,
            "recurring": recurring,
            "durable": durable,
            "createdAt": now,
        }

        if recurring:
            task['jitter_offset'] = self._compute_jitter(cron_expr)

        self.tasks.append(task)
        if durable:
            self._save_durable()

        mode = "recurring" if recurring else "one-shot"
        store = "durable" if durable else "session-only"
        return f"Create task {task_id} ({mode}, {store}): cron={cron_expr}"
    
    def delete(self, task_id: str) ->str:
        """Delete a shceduled task"""
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t["id"] != task_id]
        if len(self.tasks) < before:
            self._save_durable()
            return f"Deleted task {task_id}"
        return f"Task {task_id} not found"
    
    def list_tasks(self) -> str:
        """List all shceduled tasks"""
        if not self.tasks:
            return "No scheduled tasks"
        lines = []
        for t in self.tasks:
            mode = "recurring" if t["recurring"] else "one-shot"
            store = "durable" if t["durable"] else "session"
            age_hours = (time.time() - t["createdAt"]) / 3600
            lines.append(
                f"  {t['id']}  {t['cron']}  [{mode}/{store}] "
                f"({age_hours:.1f}h old): {t['prompt'][:60]}"
            )
        return "\n".join(lines)
    
    def drain_notifications(self) -> list[str]:
        notifications = []
        while True:
            try:
                notifications.append(self.queue.get_nowait())
            except Empty:
                break
        return notifications
    
    def _compute_jitter(self, cron_expr: str) -> int:
        """If cron targets :00 or :30, return a small offset (1-4 minutes)."""
        fields = cron_expr.strip().split()
        if len(fields) < 1:
            return 0
        minute_field = fields[0]
        try:
            minute_val = int(minute_field)
            if minute_val in JITTER_MINUTES:
                return (hash(cron_expr) % JITTER_OFFSET_MAX) + 1
        except ValueError:
            pass
        return 0
    
    def _check_loop(self):
        """Background thread: check every second if any task is due."""
        while not self._stop_event.is_set():
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute
            # Only check once per minute to avoid double-firing
            if current_minute != self._last_check_minute:
                self._last_check_minute = current_minute
                self._check_tasks(now)
            self._stop_event.wait(timeout=1)

    def _check_tasks(self, now: datetime):
        expired = []
        fired_oneshots = []

        for task in self.tasks:
            # 循环执行任务也有期限
            age_days = (time.time() - task['createdAt']) / 86400
            if task["recurring"] and age_days > AUTO_EXPIRY_DAYS:
                expired.append(task["id"])
                continue

            check_time = now
            jitter = task.get("jitter_offset", 0)
            if jitter:
                check_time = now - timedelta(minutes = jitter)

            if cron_matches(task["cron"], check_time):
                notification = (
                    f"[Scheduled task {task['id']}: {task['prompt']}]"
                )
                self.queue.put(notification)
                task['last_fired'] = time.time()
                print(f"[Cron] Fired: {task['id']}")

                if not task['recurring']:
                    fired_oneshots.append(task["id"])


    def _load_durable(self):
        """Load durable tasks from .claude/scheduled_tasks.json"""
        if not SCHEDULED_TASKS_FILE.exists():
            return 
        try:
            data = json.loads(SCHEDULED_TASKS_FILE.read_text())
            self.tasks = [t for t in data if t.get("durable")]
        except Exception as e:
            print(f"[Cron] Error loading tasks: {e}")
    
    def detect_missed_tasks(self)-> list[dict]:
        """
        处理关机时用户错过的定时任务
        """
        now = datetime.now()
        missed = []
        for task in self.tasks:
            last_fired =  task.get("last_fired")
            if last_fired is None:
                continue
            last_dt = datetime.fromtimestamp(last_fired)
            check = last_dt + timedelta(minutes=1)
            cap = min(now, last_dt + timedelta(hours=12))
            while check <= cap:
                if cron_matches(task["cron"], check):
                    missed.append({
                        "id": task["id"],
                        "cron": task["cron"],
                        "prompt": task["prompt"],
                        "missed_at": check.isoformat(),
                    })
                    break
                check += timedelta(minutes=1)
        return missed
    

    def _save_durable(self):
        durable = [t for t in self.tasks if t.get("durable")]
        SCHEDULED_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULED_TASKS_FILE.write_text(
            json.dumps(durable, indent=2) + "\n"
        )

scheduler = CronScheduler()