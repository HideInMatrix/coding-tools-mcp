from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .global_assets import global_asset_root
from .mcp_connections import MCPConnectionDefinition
from .models import PROMPT_ID_PATTERN, ResourceScope
from .recovery import quarantine_path, should_quarantine_error


LOGGER = logging.getLogger(__name__)


class MCPConnectionVersionConflictError(RuntimeError):
    def __init__(self, connection_id: str, *, expected: int, actual: int) -> None:
        self.connection_id = connection_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"MCP connection version conflict: {connection_id} expected v{expected}, current global version is v{actual}"
        )


class MCPConnectionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = global_asset_root(root)
        self.directory = self.root / "mcp-connections"

    def _path(self, connection_id: str) -> Path:
        value = connection_id.strip()
        if not PROMPT_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid MCP connection id: {connection_id!r}")
        return self.directory / f"{value}.json"

    def list(self) -> tuple[MCPConnectionDefinition, ...]:
        if not self.directory.is_dir():
            return ()
        values: list[MCPConnectionDefinition] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                values.append(self._read(path))
            except RuntimeError as exc:
                LOGGER.warning("Skipping invalid MCP Connection %s: %s", path, exc)
                if should_quarantine_error(exc):
                    quarantine_path(path, reason=str(exc))
                continue
        return tuple(sorted(values, key=lambda item: item.id))

    def get(self, connection_id: str) -> MCPConnectionDefinition | None:
        path = self._path(connection_id)
        return self._read(path) if path.is_file() else None

    def save(
        self,
        definition: MCPConnectionDefinition,
        *,
        expected_version: int,
    ) -> MCPConnectionDefinition:
        current = self.get(definition.id)
        actual = current.version if current else 0
        expected = int(expected_version)
        if expected != actual:
            raise MCPConnectionVersionConflictError(
                definition.id,
                expected=expected,
                actual=actual,
            )
        path = self._path(definition.id)
        persisted = replace(
            definition,
            version=actual + 1,
            scope=ResourceScope.GLOBAL,
            source=f"global:{path}",
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, persisted.to_dict())
        return persisted

    def delete(self, connection_id: str) -> bool:
        path = self._path(connection_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _read(self, path: Path) -> MCPConnectionDefinition:
        if path.is_symlink():
            raise RuntimeError(f"MCP Connection 文件不允许是符号链接: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"MCP Connection 文件损坏: {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"MCP Connection 文件必须是 JSON object: {path}")
        try:
            return MCPConnectionDefinition.from_mapping(
                raw,
                scope=ResourceScope.GLOBAL,
                source=f"global:{path}",
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"MCP Connection 定义无效: {path}: {exc}") from exc

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        fd, raw_temp = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temp = Path(raw_temp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

