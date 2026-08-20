from __future__ import annotations

import hashlib
import http.client
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .resources import is_frozen
from .updates import ReleaseInfo, _github_ssl_context
from .user_settings import settings_dir


UPDATE_DOWNLOAD_ATTEMPTS = 3
UPDATE_CHUNK_SIZE = 256 * 1024
UPDATE_TIMEOUT_SECONDS = 20.0
MAX_CHECKSUM_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: str = "idle"
    version: str = ""
    progress: int = 0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "version": self.version,
            "progress": self.progress,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "message": self.message,
        }


def current_install_target() -> Path:
    """Return the replaceable application root for a frozen desktop build."""

    if not is_frozen():
        raise RuntimeError("应用内更新只支持已打包的桌面程序。")

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for candidate in (executable, *executable.parents):
            if candidate.suffix.lower() == ".app":
                if len(candidate.parts) >= 2 and candidate.parts[1] == "Volumes":
                    raise RuntimeError(
                        "当前程序仍从 DMG 中运行。请先拖入 Applications，再使用应用内更新。"
                    )
                return candidate
        raise RuntimeError("无法定位当前 macOS .app 安装目录。")
    if sys.platform.startswith("win"):
        return executable
    return executable.parent


def _parse_checksum(raw: str, expected_filename: str) -> str:
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    parts = line.split()
    if not parts or len(parts[0]) != 64:
        raise ValueError("更新包 SHA-256 文件格式无效。")
    digest = parts[0].lower()
    if any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("更新包 SHA-256 文件格式无效。")
    if len(parts) >= 2:
        filename = parts[-1].lstrip("*")
        if filename != expected_filename:
            raise ValueError("SHA-256 文件与当前平台更新包不匹配。")
    return digest


def _download_checksum(url: str, expected_filename: str) -> str:
    last_error: BaseException | None = None
    for attempt in range(1, UPDATE_DOWNLOAD_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/plain, application/octet-stream;q=0.9",
                "User-Agent": "MicroMatrix-Workbench-Updater",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=UPDATE_TIMEOUT_SECONDS,
                context=_github_ssl_context(),
            ) as response:
                raw = response.read(MAX_CHECKSUM_BYTES + 1)
            if len(raw) > MAX_CHECKSUM_BYTES:
                raise ValueError("更新包 SHA-256 文件过大。")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("更新包 SHA-256 文件不是 UTF-8 文本。") from exc
            return _parse_checksum(text, expected_filename)
        except urllib.error.HTTPError:
            raise
        except (
            http.client.IncompleteRead,
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt >= UPDATE_DOWNLOAD_ATTEMPTS:
                break
            time.sleep(0.25 * attempt)
    raise RuntimeError("无法下载更新包 SHA-256 校验文件，请检查网络后重试。") from last_error


def _helper_log_path() -> Path:
    path = settings_dir() / "update-helper.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_helper_script(suffix: str, content: str) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix="micromatrix-workbench-updater-",
        suffix=suffix,
        text=True,
    )
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        if os.name != "nt":
            path.chmod(0o700)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


_POSIX_MAC_HELPER = r'''#!/bin/sh
set -eu
ARCHIVE="$1"
TARGET="$2"
PARENT_PID="$3"

while kill -0 "$PARENT_PID" 2>/dev/null; do
  sleep 0.25
done

STAGING="$(mktemp -d -t micromatrix-workbench-update.XXXXXX)"
BACKUP="${TARGET}.micromatrix-workbench-update-backup"

rollback() {
  if [ -d "$BACKUP" ]; then
    rm -rf "$TARGET" 2>/dev/null || true
    mv "$BACKUP" "$TARGET" 2>/dev/null || true
  fi
}

trap 'rollback; rm -rf "$STAGING" 2>/dev/null || true' INT TERM HUP

/usr/bin/ditto -x -k "$ARCHIVE" "$STAGING"
SOURCE="$STAGING/$(basename "$TARGET")"
if [ ! -d "$SOURCE" ]; then
  echo "Updater: extracted app bundle not found: $SOURCE" >&2
  rm -rf "$STAGING"
  /usr/bin/open "$TARGET" >/dev/null 2>&1 || true
  exit 20
fi

rm -rf "$BACKUP"
if ! mv "$TARGET" "$BACKUP"; then
  echo "Updater: failed to move current app bundle to backup" >&2
  rm -rf "$STAGING"
  /usr/bin/open "$TARGET" >/dev/null 2>&1 || true
  exit 22
fi
if ! /usr/bin/ditto "$SOURCE" "$TARGET"; then
  echo "Updater: failed to install new app bundle" >&2
  rollback
  rm -rf "$STAGING"
  /usr/bin/open "$TARGET" >/dev/null 2>&1 || true
  exit 21
fi

rm -rf "$STAGING"
rm -f "$ARCHIVE"
rmdir "$(dirname "$ARCHIVE")" 2>/dev/null || true

if ! /usr/bin/open "$TARGET"; then
  echo "Updater: new app was installed but automatic restart failed" >&2
fi

sleep 2
rm -rf "$BACKUP"
rm -f "$0"
exit 0
'''


_POSIX_LINUX_HELPER = r'''#!/bin/sh
set -eu
ARCHIVE="$1"
TARGET="$2"
PARENT_PID="$3"
EXEC_NAME="$4"

while kill -0 "$PARENT_PID" 2>/dev/null; do
  sleep 0.25
done

STAGING="$(mktemp -d -t micromatrix-workbench-update.XXXXXX)"
BACKUP="${TARGET}.micromatrix-workbench-update-backup"

rollback() {
  if [ -d "$BACKUP" ]; then
    rm -rf "$TARGET" 2>/dev/null || true
    mv "$BACKUP" "$TARGET" 2>/dev/null || true
  fi
}

trap 'rollback; rm -rf "$STAGING" 2>/dev/null || true' INT TERM HUP

tar -xzf "$ARCHIVE" -C "$STAGING"
SOURCE="$STAGING/$(basename "$TARGET")"
if [ ! -d "$SOURCE" ]; then
  echo "Updater: extracted application directory not found: $SOURCE" >&2
  rm -rf "$STAGING"
  "$TARGET/$EXEC_NAME" >/dev/null 2>&1 &
  exit 20
fi

rm -rf "$BACKUP"
if ! mv "$TARGET" "$BACKUP"; then
  echo "Updater: failed to move current application to backup" >&2
  rm -rf "$STAGING"
  "$TARGET/$EXEC_NAME" >/dev/null 2>&1 &
  exit 22
fi
if ! mv "$SOURCE" "$TARGET"; then
  echo "Updater: failed to install new application directory" >&2
  rollback
  rm -rf "$STAGING"
  "$TARGET/$EXEC_NAME" >/dev/null 2>&1 &
  exit 21
fi

rm -rf "$STAGING"
rm -f "$ARCHIVE"
rmdir "$(dirname "$ARCHIVE")" 2>/dev/null || true
"$TARGET/$EXEC_NAME" >/dev/null 2>&1 &
sleep 2
rm -rf "$BACKUP"
rm -f "$0"
exit 0
'''


def _spawn_windows_installer(installer: Path) -> None:
    """Launch the Inno Setup package and let the installer own the update.

    Windows application files must not be replaced by the running process.
    Inno Setup uses Restart Manager for files in use, performs the directory
    replacement, and starts the freshly installed application from its [Run]
    entry. The desktop process only needs to start Setup and then exit.
    """

    if not installer.is_file():
        raise RuntimeError(f"Windows 更新安装包不存在: {installer}")
    creationflags = 0
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/SP-",
        ],
        cwd=str(installer.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def _spawn_update_helper(archive: Path, target: Path) -> None:
    system = platform.system().lower()
    parent_pid = os.getpid()
    executable_name = Path(sys.executable).name
    log_path = _helper_log_path()

    if system == "darwin":
        script = _write_helper_script(".sh", _POSIX_MAC_HELPER)
        command = [
            "/bin/sh",
            str(script),
            str(archive),
            str(target),
            str(parent_pid),
        ]
        kwargs: dict[str, object] = {"start_new_session": True}
    elif system == "linux":
        script = _write_helper_script(".sh", _POSIX_LINUX_HELPER)
        command = [
            "/bin/sh",
            str(script),
            str(archive),
            str(target),
            str(parent_pid),
            executable_name,
        ]
        kwargs = {"start_new_session": True}
    else:
        raise RuntimeError(f"当前系统暂不支持自动更新: {platform.system()}")

    with log_path.open("ab") as log:
        subprocess.Popen(
            command,
            cwd=str(Path(tempfile.gettempdir())),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **kwargs,
        )


class UpdateManager:
    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._log = log or (lambda _message: None)
        self._lock = threading.RLock()
        self._status = UpdateStatus()
        self._release: ReleaseInfo | None = None
        self._archive: Path | None = None
        self._temp_dir: Path | None = None

    def status(self) -> UpdateStatus:
        with self._lock:
            return self._status

    def _set_status(self, **changes: object) -> None:
        with self._lock:
            current = self._status
            values = current.to_dict()
            values.update(changes)
            self._status = UpdateStatus(
                state=str(values["state"]),
                version=str(values["version"]),
                progress=int(values["progress"]),
                downloaded_bytes=int(values["downloaded_bytes"]),
                total_bytes=int(values["total_bytes"]),
                message=str(values["message"]),
            )

    def start(self, release: ReleaseInfo) -> UpdateStatus:
        if not is_frozen():
            raise RuntimeError("应用内更新只支持已打包安装的桌面程序。")
        if not release.update_available:
            raise RuntimeError("当前已经是最新版本。")
        if not release.update_download_url:
            raise RuntimeError(
                f"GitHub Release 缺少当前平台自动更新包: {release.update_asset_name}"
            )
        if not release.checksum_url:
            raise RuntimeError(
                f"GitHub Release 缺少校验文件: {release.update_asset_name}.sha256"
            )

        with self._lock:
            if self._status.state in {"downloading", "verifying", "installing"}:
                raise RuntimeError("更新任务正在进行中。")
            self._cleanup_download_locked()
            self._release = release
            self._status = UpdateStatus(
                state="downloading",
                version=release.latest_version,
                message="正在准备下载更新…",
            )

        threading.Thread(target=self._download_worker, daemon=True).start()
        return self.status()

    def _download_worker(self) -> None:
        release = self._release
        if release is None:
            return
        try:
            expected_sha256 = _download_checksum(
                release.checksum_url,
                release.update_asset_name,
            )
            temp_dir = Path(tempfile.mkdtemp(prefix="micromatrix-workbench-update-download-"))
            archive = temp_dir / release.update_asset_name
            self._temp_dir = temp_dir
            self._download_archive(release.update_download_url, archive)
            self._set_status(state="verifying", progress=100, message="正在验证更新包…")
            actual_sha256 = self._sha256(archive)
            if actual_sha256 != expected_sha256:
                raise RuntimeError("更新包 SHA-256 校验失败，已取消安装。")
            with self._lock:
                self._archive = archive
            self._set_status(state="ready", progress=100, message="下载完成，准备安装并重启…")
            self._log(f"更新 {release.latest_version} 下载并校验完成。")
        except Exception as exc:
            self._log(f"自动更新下载失败: {exc}")
            self._set_status(state="error", message=str(exc))

    def _download_archive(self, url: str, destination: Path) -> None:
        last_error: BaseException | None = None
        for attempt in range(1, UPDATE_DOWNLOAD_ATTEMPTS + 1):
            downloaded = 0
            total = 0
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/octet-stream",
                        "User-Agent": "MicroMatrix-Workbench-Updater",
                        "Connection": "close",
                    },
                )
                with urllib.request.urlopen(
                    request,
                    timeout=UPDATE_TIMEOUT_SECONDS,
                    context=_github_ssl_context(),
                ) as response, destination.open("wb") as output:
                    raw_length = response.headers.get("Content-Length")
                    if raw_length:
                        try:
                            total = max(0, int(raw_length))
                        except ValueError:
                            total = 0
                    while True:
                        chunk = response.read(UPDATE_CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        progress = min(99, int(downloaded * 100 / total)) if total else 0
                        self._set_status(
                            state="downloading",
                            progress=progress,
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            message="正在下载更新…",
                        )
                if total and downloaded != total:
                    raise http.client.IncompleteRead(b"", total - downloaded)
                self._set_status(
                    progress=99,
                    downloaded_bytes=downloaded,
                    total_bytes=total or downloaded,
                )
                return
            except urllib.error.HTTPError:
                destination.unlink(missing_ok=True)
                raise
            except (
                http.client.IncompleteRead,
                urllib.error.URLError,
                TimeoutError,
                socket.timeout,
                ConnectionError,
                OSError,
            ) as exc:
                last_error = exc
                destination.unlink(missing_ok=True)
                if attempt >= UPDATE_DOWNLOAD_ATTEMPTS:
                    break
                self._set_status(message=f"下载中断，正在重试 {attempt + 1}/{UPDATE_DOWNLOAD_ATTEMPTS}…")
                time.sleep(0.5 * attempt)
        raise RuntimeError("更新包下载失败，请检查网络后重试。") from last_error

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def install_and_restart(self) -> UpdateStatus:
        with self._lock:
            if self._status.state != "ready" or self._archive is None:
                raise RuntimeError("更新包尚未准备完成。")
            archive = self._archive
            version = self._status.version
        try:
            if platform.system().lower() == "windows":
                _spawn_windows_installer(archive)
                target = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MicroMatrix" / "MicroMatrix Workbench"
            else:
                target = current_install_target()
                if not os.access(target.parent, os.W_OK):
                    raise RuntimeError(
                        f"没有权限替换当前安装目录: {target.parent}。请将程序安装到当前用户可写的位置。"
                    )
                _spawn_update_helper(archive, target)
        except Exception as exc:
            self._set_status(state="error", message=str(exc))
            raise
        self._set_status(
            state="installing",
            progress=100,
            message="正在退出程序，更新完成后会自动重新启动…",
        )
        self._log(f"已启动更新助手，将安装 {version} 到 {target}")
        return self.status()

    def _cleanup_download_locked(self) -> None:
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._archive = None

    def cleanup(self) -> None:
        with self._lock:
            if self._status.state == "installing":
                # The detached helper owns the archive after install starts.
                return
            self._cleanup_download_locked()

