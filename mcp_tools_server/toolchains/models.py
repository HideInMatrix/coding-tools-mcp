from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolchainCandidate:
    kind: str
    version: str
    source: str
    root: Path
    bin_dir: Path
    executables: dict[str, str]
    selected_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "source": self.source,
            "root": str(self.root),
            "bin_dir": str(self.bin_dir),
            "executables": dict(self.executables),
            "selected_reason": self.selected_reason,
        }
