import subprocess
from pathlib import Path
from todo import TODO
import os
from memory import MemoryManager, memory_mgr
from skills import SKILL_REGISTRY
from tasks import TASKS
from backgroud import BG
from cron import scheduler

WORKDIR = Path.cwd()
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try: 
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text(encoding='utf-8')
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...({len(lines) - limit} more lines)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"
    
def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding='utf-8')
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        content = fp.read_text(encoding='utf-8')
        if old_text not in content:
            return f"Error: Text not found in {path}"
        fp.write_text(content.replace(old_text, new_text, 1),encoding='utf-8')
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
    
def run_save_memory(name: str, description: str, mem_type: str, content: str, memory_mgr: MemoryManager) ->str:
    return memory_mgr.save_memory(name, description, mem_type, content)

TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "todo":       lambda **kw: TODO.update(kw["items"]),
    "compact":    lambda **kw: "Manual compression requested.",
    "save_memory":  lambda **kw: run_save_memory(kw["name"], kw["description"], kw["type"], kw["content"], memory_mgr),
    "load_skill": lambda **kw: SKILL_REGISTRY.load_full_text(kw["name"]),
    "task_create": lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner"), kw.get("addBlockedBy"), kw.get("addBlocks")),
    "task_list":   lambda **kw: TASKS.list_all(),
    "task_get":    lambda **kw: TASKS.get(kw["task_id"]),

    "background_run":   lambda **kw: BG.run(kw["command"]),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),

    "cron_create": lambda **kw: scheduler.create(
        kw["cron"], kw["prompt"], kw.get("recurring", True), kw.get("durable", False)),
    "cron_delete": lambda **kw: scheduler.delete(kw["id"]),
    "cron_list":   lambda **kw: scheduler.list_tasks(),
}



CHILD_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object", 
            "properties": {"command": {"type": "string"}}, 
            "required": ["command"],
        }
    },
    {
        "name": "read_file", 
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",  
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, 
            "required": ["path"]
        }
    },
    {
        "name": "write_file", 
        "description": "Write content to file.",
        "input_schema": {
            "type": "object", 
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, 
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file", 
        "description": "Replace exact text in file.",
        "input_schema": {
            "type": "object", 
            "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, 
            "required": ["path", "old_text", "new_text"]
            }
    },
]

PARENT_TOOLS = CHILD_TOOLS + [
    {
        "name": "todo", 
        "description": "Update task list. Track progress on multi-step tasks.",
        "input_schema": {
            "type": "object", 
            "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["id", "text", "status"]}}}, 
            "required": ["items"]
            }
    },
    # {
    #     "name": "task", 
    #     "description": "Spawn a subagent with fresh context. It shares the filesystem but not conversation history.",
    #     "input_schema": {
    #         "type": "object", 
    #         "properties": {"prompt": {"type": "string"}, "description": {"type": "string", "description": "Short description of the task"}}, 
    #         "required": ["prompt"]
    #         }
    # },
    {
        "name": "compact", 
        "description": "Trigger manual conversation compression.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "focus": {"type": "string", "description": "What to preserve in the summary"}
            }
        }
    },
    {
        "name": "save_memory", 
        "description": "Save a persistent memory that survives across sessions.",
        "input_schema": {
            "type": "object", 
            "properties": {
                "name": {"type": "string", "description": "Short identifier (e.g. prefer_tabs, db_schema)"},
                "description": {"type": "string", "description": "One-line summary of what this memory captures"},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"],
                        "description": "user=preferences, feedback=corrections, project=non-obvious project conventions or decision reasons, reference=external resource pointers"},
                "content": {"type": "string", "description": "Full memory content (multi-line OK)"},
            }, "required": ["name", "description", "type", "content"]
     }
    },
    {
        "name": "load_skill",
        "description": "Load the full body of a named skill into the current context.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },


    {"name": "task_create", "description": "Create a new task.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
    {"name": "task_update", "description": "Update a task's status, owner, or dependencies.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "owner": {"type": "string", "description": "Set when a teammate claims the task"}, "addBlockedBy": {"type": "array", "items": {"type": "integer"}}, "addBlocks": {"type": "array", "items": {"type": "integer"}}}, "required": ["task_id"]}},
    {"name": "task_list", "description": "List all tasks with status summary.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "task_get", "description": "Get full details of a task by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},

    {"name": "background_run", "description": "Run command in background thread. Returns task_id immediately.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status. Omit task_id to list all.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},

    {"name": "cron_create", "description": "Schedule a recurring or one-shot task with a cron expression.",
     "input_schema": {"type": "object", "properties": {
         "cron": {"type": "string", "description": "5-field cron expression: 'min hour dom month dow'"},
         "prompt": {"type": "string", "description": "The prompt to inject when the task fires"},
         "recurring": {"type": "boolean", "description": "true=repeat, false=fire once then delete. Default true."},
         "durable": {"type": "boolean", "description": "true=persist to disk, false=session-only. Default false."},
     }, "required": ["cron", "prompt"]}},
    {"name": "cron_delete", "description": "Delete a scheduled task by ID.",
     "input_schema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "Task ID to delete"},
     }, "required": ["id"]}},
    {"name": "cron_list", "description": "List all scheduled tasks.",
     "input_schema": {"type": "object", "properties": {}}},
]

WRITE_TOOLS = {"write_file", "edit_file", "bash"}
READ_ONLY_TOOLS = {"read_file", "bash_readonly"}
