#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from coding_tools_launcher.resources import bundled_cloudflared_path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    cloudflared = bundled_cloudflared_path()
    if not cloudflared.exists():
        raise SystemExit(
            f"缺少 {cloudflared}\n"
            "请先运行: python scripts/fetch_cloudflared.py"
        )

    separator = ";" if sys.platform.startswith("win") else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "Coding Tools MCP",
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "coding_tools_mcp",
        "--add-binary",
        f"{cloudflared}{separator}vendor/cloudflared/{cloudflared.parent.name}",
        "desktop.py",
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
