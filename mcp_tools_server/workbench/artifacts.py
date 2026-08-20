from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .workflows import NODE_ID_PATTERN


RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    type: str
    path: str
    producer_node_id: str
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "type": self.type,
            "path": self.path,
            "producer_node_id": self.producer_node_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ArtifactRef":
        return cls(
            artifact_id=str(value.get("artifact_id") or ""),
            type=str(value.get("type") or ""),
            path=str(value.get("path") or ""),
            producer_node_id=str(value.get("producer_node_id") or ""),
            created_at=int(value.get("created_at") or 0),
        )


class ArtifactStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.run_root = self.workspace / ".coding-tools" / "runs"

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        value = run_id.strip()
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError(f"invalid workflow run id: {run_id!r}")
        return value

    def write(
        self,
        *,
        run_id: str,
        artifact_id: str,
        producer_node_id: str,
        value: Any,
        format: str,
    ) -> ArtifactRef:
        run_id = self._validate_run_id(run_id)
        artifact_id = artifact_id.strip()
        if not NODE_ID_PATTERN.fullmatch(artifact_id):
            raise ValueError(f"invalid artifact id: {artifact_id!r}")
        if not NODE_ID_PATTERN.fullmatch(producer_node_id):
            raise ValueError(f"invalid artifact producer node id: {producer_node_id!r}")
        if format not in {"json", "text"}:
            raise ValueError(f"unsupported artifact format: {format}")

        directory = self.run_root / run_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".json" if format == "json" else ".txt"
        path = directory / f"{artifact_id}{suffix}"
        if path.is_symlink():
            raise RuntimeError(f"Artifact 文件不允许是符号链接: {path}")

        if format == "json":
            try:
                content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Artifact output 不能序列化为 JSON: {artifact_id}") from exc
        else:
            if not isinstance(value, str):
                raise ValueError("text Artifact 的 source output 必须是字符串")
            content = value

        self._atomic_write(path, content)
        relative = path.relative_to(self.workspace).as_posix()
        return ArtifactRef(
            artifact_id=artifact_id,
            type=format,
            path=relative,
            producer_node_id=producer_node_id,
            created_at=int(time.time()),
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
