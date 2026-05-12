"""Workspace skills: progressive disclosure only (summary in system prompt, full SKILL.md via read_file)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


class SkillsLoader:
    """Scan ``<workspace>/skills/<name>/SKILL.md``; expose metadata for summaries only."""

    def __init__(self, workspace: Path, disabled_skills: set[str] | None = None):
        self.workspace = workspace.expanduser().resolve()
        self.workspace_skills = self.workspace / "skills"
        self.disabled_skills = disabled_skills or set()

    def list_skills(self) -> list[dict[str, str]]:
        """Entries: name, rel_path (posix, under workspace)."""
        if not self.workspace_skills.exists():
            return []
        out: list[dict[str, str]] = []
        for skill_dir in sorted(self.workspace_skills.iterdir(), key=lambda p: p.name.lower()):
            if not skill_dir.is_dir():
                continue
            name = skill_dir.name
            if name in self.disabled_skills:
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            rel = Path("skills") / name / "SKILL.md"
            out.append({"name": name, "rel_path": rel.as_posix()})
        return out

    def load_skill(self, name: str) -> str | None:
        path = self.workspace_skills / name / "SKILL.md"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end() :].strip()
        return content

    def get_skill_metadata(self, name: str) -> dict[str, Any] | None:
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict):
            return None
        return {str(k): v for k, v in parsed.items()}

    def _get_skill_description(self, name: str) -> str:
        meta = self.get_skill_metadata(name)
        if meta:
            desc = meta.get("description")
            if isinstance(desc, str) and desc.strip():
                return desc.strip()
        return name

    def build_skills_summary(self) -> str:
        """One line per skill for system prompt (no full SKILL body)."""
        lines: list[str] = []
        for entry in self.list_skills():
            name = entry["name"]
            rel = entry["rel_path"]
            desc = self._get_skill_description(name)
            lines.append(f"- **{name}** — {desc}  `{rel}`")
        return "\n".join(lines)
