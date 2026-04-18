import re
from pathlib import Path
from settings import WORKDIR
from fnmatch import fnmatch
from tools import WRITE_TOOLS,READ_ONLY_TOOLS
from settings import DEFAULT_RULES, MODES
import json
class BashSecurityValidator:
    """
    验证bash命令是否安全
    """
    VALIDATORS = [
        ("shell_metachar", r"[;&|`$]"),       # shell metacharacters
        ("sudo", r"\bsudo\b"),                 # privilege escalation
        ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),  # recursive delete
        ("cmd_substitution", r"\$\("),          # command substitution
        ("ifs_injection", r"\bIFS\s*="),        # IFS manipulation
    ]

    def validate(self, command: str) -> list:
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures
    
    def is_safe(self, command: str) -> bool:
        return len(self.validate(command)) == 0
    
    def describe_failures(self, command: str) -> str:
        failures = self.validate(command)
        if not failures:
            return "No issue detected"
        parts = [f"{name} (pattern: {pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)
    

def is_workspace_trusted(workspace: Path = None)-> bool:
    ws = workspace or WORKDIR
    trust_marker = ws / ".calude" / ".claude_trusted"
    return trust_marker.exists()


bash_validator = BashSecurityValidator()




class PermissionManager:
    def __init__(self, mode: str = "default", rules:list =None):
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}. Choose from {MODES}")
        self.mode = mode
        self.rules = rules or list(DEFAULT_RULES)

        self.consecutive_denials = 0
        self.max_consecutive_denials = 3
    
    def check(self, tool_name: str, tool_input: dict) -> dict:
        # step 0: Bash安全验证
        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = bash_validator.validate(command)
            if failures:
                severe = {"sudo", "rm_rf"}
                severe_hits = [f for f in failures if f[0] in severe]
                if severe_hits:
                    desc = bash_validator.describe_failures(command)
                    return {"behavior": "deny",
                            "reason": f"Bash validator: {desc}"}
                #除了那两个严格危险命令外
                desc = bash_validator.describe_failures(command)
                return {"behavior": "ask",
                        "reason": f"Bash validator flagged: {desc}"}

        # step 1: Deny Rule
        for rule in self.rules:
            if rule["behavior"] != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "deny",
                        "reason": f"Blocked by deny rule: {rule}"}
        
        # Step 2: Mode-based descisions
        if self.mode == "plan":
            if tool_name in WRITE_TOOLS:
                return {
                    "bahavior": "deny",
                    "reason": "Plan mode: Write operations are blocked"
                }
            return {"behavior": "allow", "reason": "Plan mode: read-only allowed"}

        if self.mode == "auto":
            if tool_name in READ_ONLY_TOOLS or tool_name == "read_file":
                return {"behavior": "allow",
                        "reason": "Auto mode: read-only tool auto-approved"}
            return {"behavior": "ask",
                "reason": f"Plan mode: asking user for {tool_name}"}
        
        # Step 3: Allow Rules
        for rule in self.rules:
            if rule["behavior"] != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                self.consecutive_denials = 0
                return {"behavior": "allow",
                        "reason": f"Matched allow rule: {rule}"}
        
         # Step 4: Ask user (default behavior for unmatched tools)
        return {"behavior": "ask",
                "reason": f"No rule matched for {tool_name}, asking user"}
    

    def ask_user(self, tool_name: str, tool_input:dict) -> bool:
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n  [Permission] {tool_name}: {preview}")
        try:
            answer = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        
        if answer == "always":
            # Add permanent allow rule for this tool
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True
        if answer in ("y", "yes"):
            self.consecutive_denials = 0
            return True
        
        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(f"  [{self.consecutive_denials} consecutive denials -- "
                  "consider switching to plan mode]")
        return False


    def _matches(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        if rule.get("tool") and rule["tool"] != "*":
            if rule["tool"] != tool_name:
                return False
        # Path pattern match
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False
        # Content pattern match (for bash commands)
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False