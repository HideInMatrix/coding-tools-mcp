"""Discover lightweight project instructions for MCP clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_INSTRUCTION_NAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
)


@dataclass(frozen=True, slots=True)
class InstructionFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ProjectContext:
    root_files: tuple[InstructionFile, ...]
    nested_files: tuple[str, ...]
    warnings: tuple[str, ...]

    def server_instructions(self) -> str:
        if not self.root_files:
            return "Operate only inside the configured workspace. Prefer read/search before edits and use apply_patch for source changes."
        parts = [
            "Operate only inside the configured workspace. Project instructions follow."
        ]
        for item in self.root_files:
            parts.append(f"\n--- {item.path} ---\n{item.content}")
        return "\n".join(parts)


def load_project_context(root: Path) -> ProjectContext:
    root_files: list[InstructionFile] = []
    warnings: list[str] = []
    for name in ROOT_INSTRUCTION_NAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Cannot read project instruction file {name}: {exc}")
            continue
        root_files.append(InstructionFile(name, content[:64_000]))

    nested: list[str] = []
    # Only record nested AGENTS.md paths. Loading their content globally would
    # incorrectly apply directory-scoped instructions to unrelated files.
    try:
        for path in root.rglob("AGENTS.md"):
            if path.parent == root:
                continue
            if any(part in {".git", ".venv", "node_modules", "dist", "build"} for part in path.relative_to(root).parts):
                continue
            nested.append(path.relative_to(root).as_posix())
            if len(nested) >= 100:
                warnings.append("Nested instruction file list truncated at 100 entries.")
                break
    except OSError as exc:
        warnings.append(f"Cannot scan nested project instructions: {exc}")

    return ProjectContext(tuple(root_files), tuple(sorted(nested)), tuple(warnings))
