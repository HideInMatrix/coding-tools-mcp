from __future__ import annotations

import http.client
import json
import platform
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


GITHUB_REPOSITORY = "HideInMatrix/micromatrix-workbench"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
GITHUB_API_VERSION = "2022-11-28"
UPDATE_CHECK_ATTEMPTS = 3
DEFAULT_GITHUB_DOWNLOAD_PROXY = "https://cdn.gh-proxy.org/"


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    current_version: str
    latest_version: str
    tag_name: str
    release_url: str
    asset_name: str
    download_url: str
    update_asset_name: str
    update_download_url: str
    checksum_url: str
    update_available: bool


def normalize_download_proxy_prefix(value: object) -> str:
    """Validate and normalize a GitHub release download proxy prefix.

    An empty value explicitly disables acceleration. A proxy is a URL prefix,
    so query strings, fragments and embedded credentials are rejected before
    it is persisted.
    """

    prefix = str(value or "").strip()
    if not prefix:
        return ""
    parsed = urllib.parse.urlsplit(prefix)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("下载加速前缀必须是有效的 http/https URL。")
    if parsed.username or parsed.password:
        raise ValueError("下载加速前缀不能包含用户名或密码。")
    if parsed.query or parsed.fragment:
        raise ValueError("下载加速前缀不能包含查询参数或锚点。")
    return prefix.rstrip("/") + "/"


def apply_download_proxy(url: str, prefix: str) -> str:
    """Prefix GitHub release assets while leaving unrelated URLs untouched."""

    normalized = normalize_download_proxy_prefix(prefix)
    if not normalized or not url:
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return url
    return normalized + url


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
        return f"MicroMatrix-Workbench-windows-{arch}.exe"
    if current_system == "darwin":
        return f"MicroMatrix-Workbench-macos-{arch}.dmg"
    if current_system == "linux":
        return f"MicroMatrix-Workbench-linux-{arch}.tar.gz"
    raise ValueError(f"不支持的系统: {current_system} {arch}")


def updater_asset_name(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the release asset consumed by the in-app updater."""

    current_system = (system or platform.system()).strip().lower()
    arch = _architecture(machine)
    if current_system == "windows":
        return f"MicroMatrix-Workbench-windows-{arch}.exe"
    if current_system == "darwin":
        return f"MicroMatrix-Workbench-macos-{arch}.zip"
    if current_system == "linux":
        return f"MicroMatrix-Workbench-linux-{arch}.tar.gz"
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


def _fetch_release_payload(
    *,
    timeout: float,
    attempts: int = UPDATE_CHECK_ATTEMPTS,
) -> dict[str, Any]:
    """Fetch the latest GitHub release with bounded transient retries.

    Packaged desktop apps may sit behind transparent proxies/VPNs that
    occasionally close an HTTPS response before the advertised Content-Length
    has been received. ``json.load(response)`` surfaces that as
    ``http.client.IncompleteRead``. Treat it like the other transient network
    failures and retry with a fresh connection instead of leaking the raw
    Python exception into the About page.
    """

    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        request = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "MicroMatrix-Workbench",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=_github_ssl_context(),
            ) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("GitHub Release API 返回了无效数据")
            return payload
        except urllib.error.HTTPError:
            # 4xx/5xx responses are actionable server/API failures; retrying a
            # rate-limit or authentication failure would only delay feedback.
            raise
        except (
            http.client.IncompleteRead,
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            last_error = exc
            if attempt >= max(1, attempts):
                break
            time.sleep(0.25 * attempt)

    retry_count = max(1, attempts)
    if isinstance(last_error, http.client.IncompleteRead):
        message = (
            f"检查 GitHub 最新版本失败：网络响应不完整，已自动重试 {retry_count} 次，"
            "请稍后再试。"
        )
    elif isinstance(last_error, (TimeoutError, socket.timeout)):
        message = (
            f"检查 GitHub 最新版本超时，已自动重试 {retry_count} 次，"
            "请检查网络后再试。"
        )
    else:
        message = (
            f"无法连接 GitHub 检查最新版本，已自动重试 {retry_count} 次，"
            "请检查网络、代理或 VPN 后再试。"
        )
    raise RuntimeError(message) from last_error


def fetch_latest_release(
    current_version: str,
    *,
    timeout: float = 8.0,
    download_proxy_prefix: str = DEFAULT_GITHUB_DOWNLOAD_PROXY,
) -> ReleaseInfo:
    payload = _fetch_release_payload(timeout=timeout)

    tag_name = str(payload.get("tag_name") or "").strip()
    if not tag_name:
        raise RuntimeError("GitHub Release 缺少 tag_name")
    latest_version = tag_name[1:] if tag_name.lower().startswith("v") else tag_name
    expected_asset = platform_asset_name()
    expected_update_asset = updater_asset_name()
    release_url = str(payload.get("html_url") or "").strip()
    proxy_prefix = normalize_download_proxy_prefix(download_proxy_prefix)
    download_url = apply_download_proxy(
        _release_asset(payload, expected_asset), proxy_prefix
    )
    update_download_url = apply_download_proxy(
        _release_asset(payload, expected_update_asset), proxy_prefix
    )
    checksum_url = apply_download_proxy(
        _release_asset(payload, expected_update_asset + ".sha256"), proxy_prefix
    )
    return ReleaseInfo(
        current_version=current_version,
        latest_version=latest_version,
        tag_name=tag_name,
        release_url=release_url,
        asset_name=expected_asset,
        download_url=download_url,
        update_asset_name=expected_update_asset,
        update_download_url=update_download_url,
        checksum_url=checksum_url,
        update_available=is_newer_version(latest_version, current_version),
    )
