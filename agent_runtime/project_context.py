"""Discover lightweight project instructions for MCP clients."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT_INSTRUCTION_NAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
)
WORKFLOW_SNAPSHOT_MAX_ITEMS = 100
WORKFLOW_SNAPSHOT_MAX_CHARS = 24_000


@dataclass(frozen=True, slots=True)
class InstructionFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ProjectContext:
    root_files: tuple[InstructionFile, ...]
    nested_files: tuple[str, ...]
    warnings: tuple[str, ...]

    def server_instructions(self, workflows: Iterable[Any] = ()) -> str:
        if not self.root_files:
            parts = [
                "Operate only inside the configured workspace. Prefer read/search before edits and use apply_patch for source changes."
            ]
        else:
            parts = [
                "Operate only inside the configured workspace. Project instructions follow."
            ]
            for item in self.root_files:
                parts.append(f"\n--- {item.path} ---\n{item.content}")

        catalog: list[str] = []
        catalog_chars = 0
        omitted = 0
        for workflow in workflows:
            if len(catalog) >= WORKFLOW_SNAPSHOT_MAX_ITEMS:
                omitted += 1
                continue
            summary = workflow.summary()
            line = (
                "- "
                f"{summary['id']}: {summary['name']} — {summary['description']} "
                f"(tags={json.dumps(summary['tags'], ensure_ascii=False)}, "
                f"inputs_schema={json.dumps(summary['inputs_schema'], ensure_ascii=False, separators=(',', ':'))})"
            )
            if catalog_chars + len(line) > WORKFLOW_SNAPSHOT_MAX_CHARS:
                omitted += 1
                continue
            catalog.append(line)
            catalog_chars += len(line)
        if omitted:
            catalog.append(
                f"- … {omitted} additional workflow(s) omitted from this snapshot; call workflow_list for the complete current catalog."
            )
        if catalog:
            parts.append(
                "\n--- MicroMatrix Workflow execution policy ---\n"
                "Workflows are user-authored operating procedures and take precedence over ad-hoc tool use for matching tasks. "
                "At the beginning of each new user task, call workflow_list because the catalog can change while this MCP connection is active. "
                "If one workflow clearly matches the user's intent, call workflow_start before using direct task tools and supply inputs that conform to its inputs_schema. "
                "Do not merely describe the workflow: execute and advance it. When a run returns waiting_model, follow the pending skill method_document as the reasoning and execution procedure, then call workflow_continue with the node result. "
                "When it returns waiting_approval, stop task execution until the signed Desktop approval is available, then call workflow_continue without inventing an approval decision. "
                "Use direct task tools only when no workflow matches or when the active workflow/skill requires them. "
                "Workflow management requests themselves may use workflow tools directly.\n"
                "Current workflow discovery snapshot:\n"
                + "\n".join(catalog)
            )
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
