#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys

from coding_tools_launcher.resources import bundled_cloudflared_path


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
        "--collect-all",
        "coding_tools_mcp",
        "--add-binary",
        f"{cloudflared}{separator}vendor/cloudflared/{cloudflared.parent.name}",
        "desktop.py",
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
