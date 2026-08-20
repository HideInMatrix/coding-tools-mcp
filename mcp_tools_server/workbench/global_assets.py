from __future__ import annotations

import os
import sys
from pathlib import Path


GLOBAL_ASSET_ROOT_ENV = "CODING_TOOLS_MCP_GLOBAL_ASSET_ROOT"


def global_asset_root(explicit: Path | None = None) -> Path:
    """Return the application-level Workbench capability asset directory."""

    if explicit is not None:
        return explicit.expanduser().resolve()

    configured = (os.environ.get(GLOBAL_ASSET_ROOT_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Coding Tools MCP"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Coding Tools MCP"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "coding-tools-mcp"
    return (base / "workbench").resolve()

