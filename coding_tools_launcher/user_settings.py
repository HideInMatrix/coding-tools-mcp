from __future__ import annotations

import json
import os
import sys
from pathlib import Path


APP_DIR_NAME = "Coding Tools MCP"


def settings_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return base / APP_DIR_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "coding-tools-mcp"


def settings_file() -> Path:
    return settings_dir() / "settings.json"


def load_settings() -> dict[str, object]:
    path = settings_file()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_settings(data: dict[str, object]) -> None:
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = settings_file()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if os.name != "nt":
        path.chmod(0o600)
