from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()
WORKDIR = Path.cwd()
MODEL =  os.environ['MODEL_ID']
SYSTEM = f"You are a coding agent at {WORKDIR}. Use the task tool to delegate exploration or subtasks."
SUBAGENT_SYSTEM = f"You are a Windows coding subagent at {WORKDIR} . Complete the given task, then summarize your findings."
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
THRESHOLD = 50000
DEFAULT_RULES = [
    # Always deny dangerous patterns
    {"tool": "bash", "content": "rm -rf /", "behavior": "deny"},
    {"tool": "bash", "content": "sudo *", "behavior": "deny"},
    # Allow reading anything
    {"tool": "read_file", "path": "*", "behavior": "allow"},
]
MODES = ("default", "plan", "auto")