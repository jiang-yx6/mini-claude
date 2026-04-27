"""Load and render agent system prompt templates (Jinja2) under nanobot/templates/.

Agent prompts live in ``templates/agent/`` (pass names like ``agent/identity.md``).
Shared copy lives under ``agent/_snippets/`` and is included via
``{% include 'agent/_snippets/....md' %}``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


@lru_cache
def _environment() -> Environment:
    # Plain-text prompts: do not HTML-escape variable values.
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def  render_template(name: str, *, strip: bool = False, **kwargs: Any) -> str:
    text = _environment().get_template(name).render(**kwargs)
    return text.rstrip() if strip else text
