from .base import Tool, tool_parameters
from pathlib import Path
WORKDIR = Path.cwd()

def safe_path(p: str) -> Path:
        path = (WORKDIR / p).resolve()
        if not path.is_relative_to(WORKDIR):
            raise ValueError(f"Path escapes workspace: {p}")
        return path

@tool_parameters(
    schema = {
        "type": "object", 
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, 
        "required": ["path"]
    }
)
class ReadFileTool(Tool):
    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Use offset and limit for large text files. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def run(self, path: str | None = None, limit: int = None) -> str:
        try:
            if not path:
                return "Error reading file: Unknown path"
            
            text = safe_path(path).read_text(encoding='utf-8')
            lines = text.splitlines()
            if limit and limit < len(lines):
                lines = lines[:limit] + [f"...({len(lines) - limit} more lines)"]
            return "\n".join(lines)[:self._MAX_CHARS]
        except Exception as e:
            return f"Error: {e}"



@tool_parameters(
    schema = { 
        "type": "object", 
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, 
        "required": ["path", "content"]
    }
)
class WriteFileTool(Tool):
    """Write content to a file."""
    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Overwrites if the file already exists; "
            "creates parent directories as needed. "
            "For partial edits, prefer edit_file instead."
        )

    async def run(self, path: str | None = None, content: str | None = None) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = safe_path(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding='utf-8')    
            return f"Successfully wrote {len(content)} characters to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"

@tool_parameters(
    schema = {
        "type": "object", 
        "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, 
        "required": ["path", "old_text", "new_text"]
    }
)
class EditFileTool(Tool):
    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace exact text in file. "
            "For partial edits, prefer edit_file instead."
        ) 

    async def run(self, 
        path: str | None = None, 
        old_text: str | None = None, 
        new_text: str | None = None):
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")
            fp = safe_path(path)
            content = fp.read_text(encoding='utf-8')
            if old_text not in content:
                raise ValueError(f"Text not found in {path}")
            fp.write_text(content.replace(old_text, new_text, 1),encoding='utf-8')
            return f"Successfully edited {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"



@tool_parameters(
    schema = {
        "type": "object", 
        "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}, "max_entries": {"type": "integer"}}, 
        "required": ["path"]
    }
)
class ListDirTool(Tool):
    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }
    
    @property
    def name(self) -> str:
        return "list_dir"

    
    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def read_only(self) -> bool:
        return True


    async def run(self, 
        path: str | None = None, 
        recursive: bool = False, 
        max_entries: int | None = None) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            dp = safe_path(path)
            if not dp.exists():
                raise ValueError(f"Directory not found: {path}")
            if not dp.is_dir():
                raise ValueError(f"Not a directory: {path}")
            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"