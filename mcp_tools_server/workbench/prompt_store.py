from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import PROMPT_ID_PATTERN, PromptDefinition, ResourceScope
from .recovery import quarantine_path, should_quarantine_error


LOGGER = logging.getLogger(__name__)


class PromptVersionConflictError(RuntimeError):
    def __init__(self, prompt_id: str, *, expected: int, actual: int) -> None:
        self.prompt_id = prompt_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Prompt version conflict: {prompt_id} expected v{expected}, "
            f"current stored version is v{actual}"
        )


class PromptStore:
    """Persistent Prompt definitions for one explicit resource scope."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        directory: Path | None = None,
        scope: ResourceScope = ResourceScope.WORKSPACE,
        source_prefix: str | None = None,
    ) -> None:
        if directory is None:
            if workspace is None:
                raise ValueError("workspace or directory is required")
            resolved_workspace = workspace.resolve()
            directory = resolved_workspace / ".coding-tools" / "prompts"
        self.directory = directory.resolve()
        self.scope = scope
        self.source_prefix = source_prefix or scope.value

    def _path(self, prompt_id: str) -> Path:
        value = prompt_id.strip()
        if not PROMPT_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid prompt id: {prompt_id!r}")
        return self.directory / f"{value}.json"

    def list(self) -> tuple[PromptDefinition, ...]:
        if not self.directory.is_dir():
            return ()
        definitions: list[PromptDefinition] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                definitions.append(self._read(path))
            except RuntimeError as exc:
                LOGGER.warning("Skipping invalid Prompt %s: %s", path, exc)
                if should_quarantine_error(exc):
                    quarantine_path(path, reason=str(exc))
        return tuple(sorted(definitions, key=lambda item: item.id))

    def get(self, prompt_id: str) -> PromptDefinition | None:
        path = self._path(prompt_id)
        if not path.is_file():
            return None
        return self._read(path)

    def save(
        self,
        prompt: PromptDefinition,
        *,
        expected_version: int,
    ) -> PromptDefinition:
        current = self.get(prompt.id)
        current_version = current.version if current is not None else 0
        expected = int(expected_version)
        if expected != current_version:
            raise PromptVersionConflictError(
                prompt.id,
                expected=expected,
                actual=current_version,
            )

        path = self._path(prompt.id)
        persisted = replace(
            prompt,
            version=current_version + 1,
            scope=self.scope,
            source=f"{self.source_prefix}:{path}",
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, persisted.to_dict())
        return persisted

    def delete(self, prompt_id: str) -> bool:
        path = self._path(prompt_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _read(self, path: Path) -> PromptDefinition:
        if path.is_symlink():
            raise RuntimeError(f"Prompt 文件不允许是符号链接: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Prompt 文件损坏: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Prompt 文件必须是 JSON object: {path}")
        try:
            return PromptDefinition.from_mapping(
                payload,
                scope=self.scope,
                source=f"{self.source_prefix}:{path}",
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Prompt 定义无效: {path}: {exc}") from exc

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        fd, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

