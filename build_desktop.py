#!/usr/bin/env python3

from __future__ import annotations

import argparse
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
MACOS_BUNDLE_IDENTIFIER = "org.micromatrix.coding-tools-mcp"
DEFAULT_WEB_DIR = ROOT / "coding_tools_launcher" / "web"
DEFAULT_WEB_DIST = DEFAULT_WEB_DIR / "dist"


def build_web_frontend() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit(
            "重新构建前端需要 Node.js/npm。请先安装 Node.js 后重新执行。"
        )
    # Desktop release packaging is standardized on npm. Local frontend
    # development may still use pnpm, but CI/release builds should not depend
    # on a globally installed pnpm binary on every target platform.
    if (DEFAULT_WEB_DIR / "package-lock.json").is_file():
        install_command = [npm, "ci", "--no-audit", "--no-fund"]
    else:
        install_command = [
            npm,
            "install",
            "--no-package-lock",
            "--no-audit",
            "--no-fund",
        ]
    subprocess.check_call(install_command, cwd=DEFAULT_WEB_DIR)
    subprocess.check_call([npm, "run", "build"], cwd=DEFAULT_WEB_DIR)


def resolve_web_dist(path: str | None) -> Path:
    web_dist = Path(path).expanduser().resolve() if path else DEFAULT_WEB_DIST
    entrypoint = web_dist / "index.html"
    if not entrypoint.is_file():
        raise SystemExit(
            f"找不到前端构建产物: {entrypoint}\n"
            "桌面打包默认复用已构建的 Vue dist。请先执行:\n"
            "  cd coding_tools_launcher/web && npm install --no-package-lock && npm run build\n"
            "或使用 build_desktop.py --build-web。"
        )
    return web_dist


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the Coding Tools MCP desktop bundle.")
    web_source = parser.add_mutually_exclusive_group()
    web_source.add_argument(
        "--build-web",
        action="store_true",
        help="Install/build the Vue frontend with npm before desktop packaging.",
    )
    web_source.add_argument(
        "--web-dist",
        metavar="PATH",
        help="Use an existing frontend dist artifact instead of coding_tools_launcher/web/dist.",
    )
    return parser.parse_args(argv)


def pyinstaller_bundle_mode(platform_name: str | None = None) -> str:
    current = (platform_name or sys.platform).lower()
    return "--onefile" if current.startswith("win") else "--onedir"


def resolve_build_version() -> str:
    """Resolve the version embedded into the desktop bundle.

    Tag builds use GitHub's GITHUB_REF_NAME (for example ``v0.1.4``).
    CODING_TOOLS_RELEASE_VERSION is provided as an explicit local/CI override.
    Local builds use the semantic Git tag attached to HEAD. The version of the
    mcp_tools_server package is intentionally unrelated to the desktop release.
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.build_web:
        build_web_frontend()
    web_dist = resolve_web_dist(args.web_dist)

    cloudflared = bundled_cloudflared_path()
    if not cloudflared.exists():
        raise SystemExit(
            f"缺少 {cloudflared}\n"
            "请先运行: python scripts/fetch_cloudflared.py"
        )

    build_version = resolve_build_version()
    version_file = write_build_version(build_version)
    separator = ";" if sys.platform.startswith("win") else ":"
    bundle_mode = pyinstaller_bundle_mode()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        bundle_mode,
        "--windowed",
        "--name",
        "Coding Tools MCP",
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "mcp_tools_server",
        "--collect-all",
        "webview",
        "--add-binary",
        f"{cloudflared}{separator}vendor/cloudflared/{cloudflared.parent.name}",
        "--add-data",
        f"{version_file}{separator}coding_tools_launcher",
        "--add-data",
        f"{web_dist}{separator}coding_tools_launcher/web/dist",
    ]
    if sys.platform == "darwin":
        command.extend(
            [
                "--osx-bundle-identifier",
                MACOS_BUNDLE_IDENTIFIER,
            ]
        )
    command.append("desktop.py")
    print(f"Desktop build version: {build_version}")
    print(f"Frontend dist: {web_dist}")
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
