from __future__ import annotations

import re
import subprocess
from pathlib import Path

BUILD_VERSION_FILENAME = "build-version.txt"
DEV_VERSION = "0.0.0-dev"


def normalize_version(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", normalized):
        raise ValueError(f"无法识别版本号: {value}")
    return normalized


def git_release_version(repo_root: Path | None = None) -> str | None:
    """Return the semantic version tag attached to the current Git commit."""

    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return normalize_version(result.stdout.strip())
    except ValueError:
        return None


def current_version() -> str:
    """Return the desktop release version, independent from MCP core versioning."""

    build_file = Path(__file__).resolve().with_name(BUILD_VERSION_FILENAME)
    try:
        raw = build_file.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            return normalize_version(raw)
        except ValueError:
            pass

    git_version = git_release_version()
    return git_version or DEV_VERSION
