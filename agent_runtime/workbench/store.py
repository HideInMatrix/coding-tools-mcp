from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import ResourceScope
from .recovery import quarantine_path, should_quarantine_error
from .workflows import WORKFLOW_ID_PATTERN, WorkflowDefinition


LOGGER = logging.getLogger(__name__)


class WorkflowVersionConflictError(RuntimeError):
    def __init__(self, workflow_id: str, *, expected: int, actual: int) -> None:
        self.workflow_id = workflow_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Workflow version conflict: {workflow_id} expected v{expected}, current workspace version is v{actual}"
        )


class WorkflowStore:
    """Workspace-scoped persistent Workflow definitions."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.directory = self.workspace / ".micromatrix-workbench" / "workflows"

    def _path(self, workflow_id: str) -> Path:
        value = workflow_id.strip()
        if not WORKFLOW_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid workflow id: {workflow_id!r}")
        return self.directory / f"{value}.json"

    def list(self) -> tuple[WorkflowDefinition, ...]:
        if not self.directory.is_dir():
            return ()
        definitions: list[WorkflowDefinition] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                definitions.append(self._read(path))
            except RuntimeError as exc:
                LOGGER.warning("Skipping invalid Workflow %s: %s", path, exc)
                if should_quarantine_error(exc):
                    quarantine_path(path, reason=str(exc))
        return tuple(sorted(definitions, key=lambda item: item.id))

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        path = self._path(workflow_id)
        if not path.is_file():
            return None
        return self._read(path)

    def save(
        self,
        workflow: WorkflowDefinition,
        *,
        expected_version: int | None = None,
    ) -> WorkflowDefinition:
        current = self.get(workflow.id)
        current_version = current.version if current is not None else 0
        if expected_version is not None and int(expected_version) != current_version:
            raise WorkflowVersionConflictError(
                workflow.id,
                expected=int(expected_version),
                actual=current_version,
            )
        version = current_version + 1
        path = self._path(workflow.id)
        persisted = replace(
            workflow,
            version=version,
            scope=ResourceScope.WORKSPACE,
            source=f"workspace:{path}",
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, persisted.to_dict())
        return persisted

    def delete(self, workflow_id: str) -> bool:
        path = self._path(workflow_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _read(self, path: Path) -> WorkflowDefinition:
        if path.is_symlink():
            raise RuntimeError(f"Workflow 文件不允许是符号链接: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Workflow 文件损坏: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Workflow 文件必须是 JSON object: {path}")
        try:
            return WorkflowDefinition.from_mapping(
                payload,
                scope=ResourceScope.WORKSPACE,
                source=f"workspace:{path}",
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Workflow 定义无效: {path}: {exc}") from exc

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

