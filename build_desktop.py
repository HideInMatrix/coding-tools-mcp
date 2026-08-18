#!/usr/bin/env python3

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from coding_tools_launcher.resources import bundled_cloudflared_path
from coding_tools_launcher.version import (
    BUILD_VERSION_FILENAME,
    DEV_VERSION,
    git_release_version,
    normalize_version,
)


ROOT = Path(__file__).resolve().parent


def build_web_frontend() -> None:
    web_dir = ROOT / "coding_tools_launcher" / "web"
    pnpm_lock = web_dir / "pnpm-lock.yaml"
    if pnpm_lock.is_file():
        pnpm = shutil.which("pnpm")
        if not pnpm:
            raise SystemExit(
                "检测到 coding_tools_launcher/web/pnpm-lock.yaml，但当前环境没有 pnpm。"
                "请安装 pnpm 后重新执行 build_desktop.py。"
            )
        subprocess.check_call([pnpm, "install", "--frozen-lockfile"], cwd=web_dir)
        subprocess.check_call([pnpm, "run", "build"], cwd=web_dir)
        return

    npm = shutil.which("npm")
    if not npm:
        raise SystemExit(
            "构建桌面版需要 Node.js/npm。请安装 Node.js 后重新执行 build_desktop.py。"
        )
    if (web_dir / "package-lock.json").is_file():
        install_command = [npm, "ci", "--no-audit", "--no-fund"]
    else:
        install_command = [
            npm,
            "install",
            "--no-package-lock",
            "--no-audit",
            "--no-fund",
        ]
    subprocess.check_call(install_command, cwd=web_dir)
    subprocess.check_call([npm, "run", "build"], cwd=web_dir)


def resolve_build_version() -> str:
    """Resolve the version embedded into the desktop bundle.

    Tag builds use GitHub's GITHUB_REF_NAME (for example ``v0.1.4``).
    CODING_TOOLS_RELEASE_VERSION is provided as an explicit local/CI override.
    Local builds use the semantic Git tag attached to HEAD. The version of the
    coding_tools_mcp package is intentionally unrelated to the desktop release.
    """

    candidates = (
        os.environ.get("CODING_TOOLS_RELEASE_VERSION", ""),
        os.environ.get("GITHUB_REF_NAME", ""),
    )
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return normalize_version(candidate)
        except ValueError:
            continue

    return git_release_version(ROOT) or DEV_VERSION


def write_build_version(version: str) -> Path:
    metadata_dir = ROOT / ".build-meta"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    path = metadata_dir / BUILD_VERSION_FILENAME
    path.write_text(f"{version}\n", encoding="utf-8")
    return path


def main() -> int:
    build_web_frontend()
    cloudflared = bundled_cloudflared_path()
    if not cloudflared.exists():
        raise SystemExit(
            f"缺少 {cloudflared}\n"
            "请先运行: python scripts/fetch_cloudflared.py"
        )

    build_version = resolve_build_version()
    version_file = write_build_version(build_version)
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
        "--collect-all",
        "webview",
        "--add-binary",
        f"{cloudflared}{separator}vendor/cloudflared/{cloudflared.parent.name}",
        "--add-data",
        f"{version_file}{separator}coding_tools_launcher",
        "--add-data",
        f"{ROOT / 'coding_tools_launcher' / 'web' / 'dist'}{separator}coding_tools_launcher/web/dist",
        "desktop.py",
    ]
    print(f"Desktop build version: {build_version}")
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
