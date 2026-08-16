"""Workspace confinement and filesystem traversal helpers."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .errors import ToolError


DEFAULT_IGNORED_NAMES = frozenset({".git", ".venv", "venv", "node_modules", "dist", "build", "target", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    absolute: Path
    display: str


class Workspace:
    def __init__(self, root: Path):
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"workspace is not a directory: {root}")
        self.root = root

    def _resolve(self, raw: str, *, require_exists: bool) -> ResolvedPath:
        candidate = Path(raw or ".").expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            absolute = candidate.resolve(strict=False)
        except OSError as exc:
            raise ToolError("PATH_ERROR", f"cannot resolve path: {raw}", "filesystem", details={"error": str(exc)}) from exc
        try:
            relative = absolute.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("PATH_OUTSIDE_WORKSPACE", f"path escapes the configured workspace: {raw}", "permission", False, {"workspace": str(self.root)}) from exc
        if require_exists and not absolute.exists():
            raise ToolError("NOT_FOUND", f"path does not exist: {raw}", "filesystem")
        if require_exists:
            try:
                absolute = candidate.resolve(strict=True)
                relative = absolute.relative_to(self.root)
            except ValueError as exc:
                raise ToolError("PATH_OUTSIDE_WORKSPACE", f"path escapes the configured workspace: {raw}", "permission", False, {"workspace": str(self.root)}) from exc
            except OSError as exc:
                raise ToolError("PATH_ERROR", f"cannot resolve path: {raw}", "filesystem", details={"error": str(exc)}) from exc
        return ResolvedPath(absolute, "." if relative == Path(".") else relative.as_posix())

    def existing(self, raw: str = ".") -> ResolvedPath:
        return self._resolve(raw, require_exists=True)

    def writable(self, raw: str) -> ResolvedPath:
        return self._resolve(raw, require_exists=False)

    @staticmethod
    def hidden(relative: Path) -> bool:
        return any(part.startswith(".") and part not in {".", ".."} for part in relative.parts)

    @staticmethod
    def ignored(relative: Path) -> bool:
        return any(part in DEFAULT_IGNORED_NAMES for part in relative.parts)

    def iter_files(self, base: ResolvedPath, *, include_hidden: bool, include_ignored: bool) -> Iterator[tuple[Path, Path]]:
        if base.absolute.is_file():
            yield base.absolute, base.absolute.relative_to(self.root)
            return
        for directory, names, files in os.walk(base.absolute, followlinks=False):
            directory_path = Path(directory)
            root_relative = directory_path.relative_to(self.root)
            names[:] = [name for name in names if (include_hidden or not self.hidden(root_relative / name)) and (include_ignored or not self.ignored(root_relative / name))]
            for name in files:
                absolute = directory_path / name
                relative = absolute.relative_to(self.root)
                if not include_hidden and self.hidden(relative):
                    continue
                if not include_ignored and self.ignored(relative):
                    continue
                yield absolute, relative


def matches_any(path: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    name = Path(path).name
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)
