from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root) if bundle_root else PROJECT_ROOT


def web_ui_entrypoint() -> Path:
    return resource_root() / "agent_workbench" / "web" / "dist" / "index.html"


def platform_tag() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = machine
    names = {"darwin": "darwin", "windows": "windows", "linux": "linux"}
    if system not in names:
        raise RuntimeError(f"暂不支持的平台: {platform.system()} {machine}")
    return f"{names[system]}-{arch}"


def bundled_executable_path(product: str, executable_name: str) -> Path:
    filename = executable_name
    if os.name == "nt" and not filename.lower().endswith(".exe"):
        filename += ".exe"
    return resource_root() / "vendor" / product / platform_tag() / filename


def bundled_cloudflared_path() -> Path:
    return bundled_executable_path("cloudflared", "cloudflared")


def resolve_cloudflared() -> Path:
    bundled = bundled_cloudflared_path()
    if bundled.is_file():
        if os.name != "nt":
            bundled.chmod(bundled.stat().st_mode | 0o111)
        return bundled

    if is_frozen():
        raise RuntimeError(
            "应用包中缺少 cloudflared。请重新安装完整版本的 MicroMatrix Workbench。"
        )

    import shutil

    executable = shutil.which("cloudflared")
    if executable:
        return Path(executable)
    raise RuntimeError(
        "开发环境未找到 cloudflared。可以安装到 PATH，或把二进制放到 vendor/cloudflared/<平台>/。"
    )
