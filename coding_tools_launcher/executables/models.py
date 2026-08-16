from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExecutableSpec:
    key: str
    display_name: str
    executable_name: str
    bundled_product: str
    version_args: tuple[str, ...]
    version_markers: tuple[str, ...] = ()
    extra_known_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutableCandidate:
    path: Path
    source: str
    version: str
    verified: bool = True
    warning: str = ""
    details: dict[str, str] = field(default_factory=dict)

    @property
    def source_label(self) -> str:
        labels = {
            "manual": "手动指定",
            "bundled": "应用内置",
            "standard": "标准安装目录",
            "path": "系统 PATH",
        }
        return labels.get(self.source, self.source)
