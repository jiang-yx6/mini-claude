"""Load ``config.json`` from the EthanAgent workspace (or ``ETHAN_CONFIG_PATH``)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

from config.schema import Config


def load_config(workspace: Path) -> Config:
    """Parse workspace ``config.json``; missing or invalid file returns defaults."""
    override = os.environ.get("ETHAN_CONFIG_PATH", "").strip()
    path = Path(override).expanduser().resolve() if override else (workspace / "config.json")
    if not path.is_file():
        return Config()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.warning("EthanAgent config at {}: root must be an object, using defaults", path)
            return Config()
        return Config.model_validate(data)
    except json.JSONDecodeError as e:
        logger.warning("EthanAgent config at {}: invalid JSON ({}), using defaults", path, e)
        return Config()
    except Exception as e:
        logger.warning("EthanAgent config at {}: failed to load ({}), using defaults", path, e)
        return Config()
