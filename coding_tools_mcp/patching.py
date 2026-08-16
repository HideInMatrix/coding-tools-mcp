"""Project-owned atomic patch engine."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ToolError
from .workspace import Workspace


HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")


@dataclass(slots=True)
class PatchSection:
    operation: str
    path: str
    body: list[str]


@dataclass(slots=True)
class PreparedChange:
    operation: str
    path: Path
    display: str
    original: bytes | None
    replacement: bytes | None
    additions: int = 0
    removals: int = 0


def parse_envelope(text: str) -> list[PatchSection]:
    lines = text.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ToolError("INVALID_PATCH", "patch must use *** Begin Patch / *** End Patch", "validation")
    sections: list[PatchSection] = []
    current: PatchSection | None = None
    for line in lines[1:-1]:
        match = HEADER_RE.match(line)
        if match:
            current = PatchSection(match.group(1).lower(), match.group(2).strip(), [])
            sections.append(current)
        elif current is None:
            if line.strip():
                raise ToolError("INVALID_PATCH", "content appears before the first file section", "validation")
        else:
            current.body.append(line)
    if not sections:
        raise ToolError("INVALID_PATCH", "patch contains no file sections", "validation")
    return sections


def _split_hunks(body: list[str]) -> list[list[str]]:
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        hunks.append(current)
    return hunks or [[]]


def _sequences(hunk: list[str]) -> tuple[list[str], list[str], int, int]:
    old: list[str] = []
    new: list[str] = []
    additions = removals = 0
    for line in hunk:
        if line.startswith("+"):
            new.append(line[1:])
            additions += 1
        elif line.startswith("-"):
            old.append(line[1:])
            removals += 1
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
        else:
            old.append(line)
            new.append(line)
    return old, new, additions, removals


def _locate(lines: list[str], needle: list[str], start: int) -> int:
    if not needle:
        return start
    for index in list(range(start, max(start, len(lines) - len(needle) + 1))) + list(range(0, max(0, start))):
        if lines[index : index + len(needle)] == needle:
            return index
    return -1


def _updated_text(original: str, body: list[str]) -> tuple[str, int, int]:
    final_newline = original.endswith("\n")
    lines = original.splitlines()
    cursor = 0
    additions = removals = 0
    for hunk in _split_hunks(body):
        old, new, add_count, remove_count = _sequences(hunk)
        index = _locate(lines, old, cursor)
        if index < 0:
            raise ToolError("PATCH_CONTEXT_MISMATCH", "update hunk context was not found", "conflict", True, {"context": "\n".join(old[:8])})
        lines[index : index + len(old)] = new
        cursor = index + len(new)
        additions += add_count
        removals += remove_count
    result = "\n".join(lines)
    if final_newline:
        result += "\n"
    return result, additions, removals


def prepare(workspace: Workspace, patch_text: str) -> list[PreparedChange]:
    changes: list[PreparedChange] = []
    seen: set[Path] = set()
    for section in parse_envelope(patch_text):
        resolved = workspace.writable(section.path)
        path = resolved.absolute
        if path in seen:
            raise ToolError("INVALID_PATCH", f"multiple sections target the same file: {section.path}", "validation")
        seen.add(path)
        original = path.read_bytes() if path.is_file() else None
        if section.operation == "add":
            if path.exists():
                raise ToolError("ALREADY_EXISTS", f"cannot add existing file: {section.path}", "conflict")
            output: list[str] = []
            for line in section.body:
                if not line.startswith("+") and line:
                    raise ToolError("INVALID_PATCH", "Add File lines must start with '+'", "validation")
                output.append(line[1:] if line.startswith("+") else "")
            replacement = ("\n".join(output) + ("\n" if section.body else "")).encode("utf-8")
            changes.append(PreparedChange("add", path, resolved.display, None, replacement, len(output), 0))
        elif section.operation == "delete":
            if original is None:
                raise ToolError("NOT_FOUND", f"cannot delete missing file: {section.path}", "filesystem")
            changes.append(PreparedChange("delete", path, resolved.display, original, None, 0, len(original.decode("utf-8", "replace").splitlines())))
        else:
            if original is None:
                raise ToolError("NOT_FOUND", f"cannot update missing file: {section.path}", "filesystem")
            try:
                text = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError("NOT_TEXT", f"cannot patch non-UTF-8 file: {section.path}", "validation") from exc
            updated, additions, removals = _updated_text(text, section.body)
            changes.append(PreparedChange("update", path, resolved.display, original, updated.encode("utf-8"), additions, removals))
    return changes


def commit(changes: list[PreparedChange]) -> None:
    completed: list[PreparedChange] = []
    try:
        for change in changes:
            if change.replacement is None:
                change.path.unlink()
            else:
                change.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=f".{change.path.name}.", dir=change.path.parent)
                temp_path = Path(temporary)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(change.replacement)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, change.path)
                finally:
                    temp_path.unlink(missing_ok=True)
            completed.append(change)
    except Exception as exc:
        for change in reversed(completed):
            try:
                if change.original is None:
                    change.path.unlink(missing_ok=True)
                else:
                    change.path.parent.mkdir(parents=True, exist_ok=True)
                    change.path.write_bytes(change.original)
            except OSError:
                pass
        raise ToolError("PATCH_COMMIT_FAILED", "patch commit failed; rollback was attempted", "filesystem", True, {"error": str(exc)}) from exc


def apply_patch(workspace: Workspace, patch_text: str, *, dry_run: bool) -> dict[str, object]:
    changes = prepare(workspace, patch_text)
    if not dry_run:
        commit(changes)
    additions = sum(item.additions for item in changes)
    removals = sum(item.removals for item in changes)
    return {
        "dry_run": dry_run,
        "affected_files": [{"operation": item.operation, "path": item.display} for item in changes],
        "additions": additions,
        "removals": removals,
        "summary": f"{len(changes)} file(s), +{additions} -{removals}",
    }