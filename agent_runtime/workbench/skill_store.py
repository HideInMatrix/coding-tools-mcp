from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .models import ResourceScope, WORKBENCH_ID_PATTERN
from .recovery import quarantine_path, should_quarantine_error
from .skills import SkillDefinition


LOGGER = logging.getLogger(__name__)


class SkillVersionConflictError(RuntimeError):
    def __init__(self, skill_id: str, *, expected: int, actual: int) -> None:
        self.skill_id = skill_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Skill version conflict: {skill_id} expected v{expected}, "
            f"current stored version is v{actual}"
        )


class SkillStore:
    """Persistent Skill assets using skill.json + SKILL.md."""

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
            directory = resolved_workspace / ".micromatrix-workbench" / "skills"
        self.directory = directory.resolve()
        self.scope = scope
        self.source_prefix = source_prefix or scope.value

    def _directory(self, skill_id: str) -> Path:
        value = skill_id.strip()
        if not WORKBENCH_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid skill id: {skill_id!r}")
        return self.directory / value

    def list(self) -> tuple[SkillDefinition, ...]:
        if not self.directory.is_dir():
            return ()
        definitions: list[SkillDefinition] = []
        for path in sorted(item for item in self.directory.iterdir() if item.is_dir()):
            try:
                definitions.append(self._read(path))
            except RuntimeError as exc:
                LOGGER.warning("Skipping invalid Skill %s: %s", path, exc)
                if should_quarantine_error(exc):
                    quarantine_path(path, reason=str(exc))
        return tuple(sorted(definitions, key=lambda item: item.id))

    def get(self, skill_id: str) -> SkillDefinition | None:
        path = self._directory(skill_id)
        if not path.is_dir():
            return None
        return self._read(path)

    def save(
        self,
        skill: SkillDefinition,
        *,
        expected_version: int,
    ) -> SkillDefinition:
        current = self.get(skill.id)
        current_version = current.version if current is not None else 0
        expected = int(expected_version)
        if expected != current_version:
            raise SkillVersionConflictError(
                skill.id,
                expected=expected,
                actual=current_version,
            )

        destination = self._directory(skill.id)
        persisted = replace(
            skill,
            version=current_version + 1,
            scope=self.scope,
            source=f"{self.source_prefix}:{destination}",
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{skill.id}.", suffix=".tmp", dir=self.directory)
        )
        backup = self.directory / f".{skill.id}.backup"
        try:
            self._write_text(
                temporary / "skill.json",
                json.dumps(
                    persisted.metadata_dict(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
            self._write_text(temporary / "SKILL.md", persisted.method_document.rstrip() + "\n")

            if backup.exists():
                shutil.rmtree(backup)
            if destination.exists():
                os.replace(destination, backup)
            os.replace(temporary, destination)
            if backup.exists():
                shutil.rmtree(backup)
        except Exception:
            if not destination.exists() and backup.exists():
                os.replace(backup, destination)
            raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            if backup.exists() and destination.exists():
                shutil.rmtree(backup)

        return persisted

    def delete(self, skill_id: str) -> bool:
        path = self._directory(skill_id)
        if not path.is_dir():
            return False
        if path.is_symlink():
            raise ValueError(f"Skill directory must not be a symlink: {path}")
        shutil.rmtree(path)
        return True

    def _read(self, path: Path) -> SkillDefinition:
        if path.is_symlink():
            raise RuntimeError(f"Skill 目录不允许是符号链接: {path}")
        metadata_file = path / "skill.json"
        method_file = path / "SKILL.md"
        if not metadata_file.is_file() or not method_file.is_file():
            raise RuntimeError(f"Skill 必须同时包含 skill.json 和 SKILL.md: {path}")
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
            method = method_file.read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Skill 文件损坏: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Skill skill.json 必须是 JSON object: {metadata_file}")
        try:
            return SkillDefinition.from_mapping(
                payload,
                method_document=method,
                scope=self.scope,
                source=f"{self.source_prefix}:{path}",
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Skill 定义无效: {path}: {exc}") from exc

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

