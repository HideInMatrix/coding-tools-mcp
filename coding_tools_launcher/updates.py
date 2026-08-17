from __future__ import annotations

import json
import platform
import re
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Any


GITHUB_REPOSITORY = "HideInMatrix/coding-tools-mcp"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    current_version: str
    latest_version: str
    tag_name: str
    release_url: str
    asset_name: str
    download_url: str
    update_available: bool


def _github_ssl_context() -> ssl.SSLContext:
    """Build a TLS context that also works inside the packaged desktop app."""

    try:
        import certifi
    except ImportError:
        # Source/development environments can still use the operating system CA
        # store. Release builds install certifi from requirements-desktop.txt.
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _version_tuple(value: str) -> tuple[int, int, int]:
    normalized = value.strip()
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", normalized)
    if match is None:
        raise ValueError(f"无法识别版本号: {value}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def _architecture(machine: str | None = None) -> str:
    raw = (machine or platform.machine()).strip().lower()
    if raw in {"x86_64", "amd64"}:
        return "x64"
    if raw in {"arm64", "aarch64"}:
        return "arm64"
    if raw in {"x86", "i386", "i686"}:
        return "x86"
    return raw.replace(" ", "-")


def platform_asset_name(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    current_system = (system or platform.system()).strip().lower()
    arch = _architecture(machine)
    if current_system == "windows":
        return f"Coding-Tools-MCP-windows-{arch}.zip"
    if current_system == "darwin":
        return f"Coding-Tools-MCP-macos-{arch}.dmg"
    if current_system == "linux":
        return f"Coding-Tools-MCP-linux-{arch}.tar.gz"
    raise ValueError(f"不支持的系统: {current_system} {arch}")


def _release_asset(payload: dict[str, Any], expected_name: str) -> str:
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != expected_name:
            continue
        url = asset.get("browser_download_url")
        return str(url) if isinstance(url, str) else ""
    return ""


def fetch_latest_release(
    current_version: str,
    *,
    timeout: float = 8.0,
) -> ReleaseInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Coding-Tools-MCP",
        },
    )
    # PyInstaller 打包后的 Python 运行时在部分 macOS/Windows 环境中无法可靠
    # 找到系统 CA，直接使用 urllib 会出现 CERTIFICATE_VERIFY_FAILED。
    # certifi 随桌面包一起分发，显式指定 CA 文件可保证 GitHub HTTPS 校验一致。
    ssl_context = _github_ssl_context()
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=ssl_context,
    ) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub Release API 返回了无效数据")

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise RuntimeError("GitHub Release 缺少 tag_name")
    latest_version = tag_name[1:] if tag_name.lower().startswith("v") else tag_name
    expected_asset = platform_asset_name()
    release_url = str(payload.get("html_url") or "").strip()
    download_url = _release_asset(payload, expected_asset)
    return ReleaseInfo(
        current_version=current_version,
        latest_version=latest_version,
        tag_name=tag_name,
        release_url=release_url,
        asset_name=expected_asset,
        download_url=download_url,
        update_available=is_newer_version(latest_version, current_version),
    )
