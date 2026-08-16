from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ..process_utils import hidden_process_kwargs
from .models import ExecutableCandidate, ExecutableSpec


VERSION_PATTERN = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?\b")


def _safe_real_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_file():
        raise RuntimeError(f"客户端文件不存在: {expanded}")
    resolved = expanded.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError(f"客户端路径不是普通文件: {resolved}")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise RuntimeError(f"客户端文件不可执行: {resolved}")
    if os.name == "nt" and resolved.suffix.lower() != ".exe":
        raise RuntimeError(f"Windows 客户端必须是 .exe 文件: {resolved}")
    return resolved


def _run_probe(command: list[str], *, timeout: float = 3.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            **hidden_process_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"客户端验证失败: {exc}") from exc
    output = (completed.stdout or "").strip()
    return completed.returncode, output


def _extract_version(output: str) -> str:
    match = VERSION_PATTERN.search(output)
    if match:
        return match.group(0)
    first_line = output.splitlines()[0].strip() if output else ""
    return first_line[:120]


def verify_executable(
    spec: ExecutableSpec,
    path: Path,
    *,
    source: str,
) -> ExecutableCandidate:
    resolved = _safe_real_path(path)
    return_code, output = _run_probe([str(resolved), *spec.version_args])
    if return_code != 0:
        raise RuntimeError(
            f"{spec.display_name} 版本检测失败，退出码: {return_code}"
        )
    if not output:
        raise RuntimeError(f"{spec.display_name} 没有返回版本信息。")

    lowered = output.lower()
    if spec.version_markers and not any(
        marker.lower() in lowered for marker in spec.version_markers
    ):
        raise RuntimeError(
            f"检测到的文件无法确认是 {spec.display_name}: {resolved}"
        )
    if not VERSION_PATTERN.search(output):
        raise RuntimeError(
            f"{spec.display_name} 返回了无法识别的版本信息: {output[:120]}"
        )

    warning = ""
    details: dict[str, str] = {}
    if spec.key == "tailscale":
        warning, details = _tailscale_status(resolved)

    return ExecutableCandidate(
        path=resolved,
        source=source,
        version=_extract_version(output),
        verified=True,
        warning=warning,
        details=details,
    )


def _tailscale_status(path: Path) -> tuple[str, dict[str, str]]:
    return_code, output = _run_probe([str(path), "status", "--json"])
    if return_code != 0 or not output:
        return "客户端已安装，但 Tailscale 服务未就绪或尚未登录。", {
            "service": "unavailable"
        }
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return "客户端已安装，但无法解析 Tailscale 状态。", {
            "service": "unknown"
        }
    backend_state = str(payload.get("BackendState", "")).strip()
    if backend_state.lower() == "running":
        return "", {"service": "running", "backend_state": backend_state}
    if backend_state:
        return f"Tailscale 当前状态: {backend_state}", {
            "service": "not-ready",
            "backend_state": backend_state,
        }
    return "客户端已安装，但尚未确认 Tailscale 登录状态。", {
        "service": "unknown"
    }
