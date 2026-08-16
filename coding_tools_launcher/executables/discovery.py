from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from ..resources import bundled_executable_path
from .models import ExecutableSpec


def _with_windows_suffix(name: str) -> str:
    if os.name == "nt" and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def _standard_directories() -> list[Path]:
    home = Path.home()
    system = platform.system().lower()
    if system == "darwin":
        return [
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            home / ".local" / "bin",
        ]
    if system == "linux":
        return [
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            home / ".local" / "bin",
            Path("/opt/bin"),
        ]
    if system == "windows":
        result: list[Path] = []
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            raw = os.environ.get(variable, "").strip()
            if raw:
                result.append(Path(raw))
        result.extend(
            [
                home / "scoop" / "shims",
                Path(os.environ.get("ProgramData", "C:/ProgramData"))
                / "chocolatey"
                / "bin",
            ]
        )
        return result
    return []


def bundled_candidate(spec: ExecutableSpec) -> Path | None:
    path = bundled_executable_path(spec.bundled_product, spec.executable_name)
    return path if path.is_file() else None


def standard_candidates(spec: ExecutableSpec) -> list[Path]:
    filename = _with_windows_suffix(spec.executable_name)
    candidates: list[Path] = []

    for raw in spec.extra_known_paths:
        candidates.append(Path(raw).expanduser())

    for directory in _standard_directories():
        candidates.append(directory / filename)
        if os.name == "nt":
            candidates.extend(
                [
                    directory / spec.executable_name / filename,
                    directory / "Programs" / spec.executable_name / filename,
                ]
            )

    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            result.append(candidate)
    return result


def path_candidate(spec: ExecutableSpec) -> Path | None:
    resolved = shutil.which(spec.executable_name)
    return Path(resolved) if resolved else None
