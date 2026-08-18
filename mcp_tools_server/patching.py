"""Project-owned atomic patch engine."""

from __future__ import annotations

import os
import re
import stat
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
    move_to: str | None = None


@dataclass(slots=True)
class PreparedChange:
    operation: str
    path: Path
    display: str
    original: bytes | None
    replacement: bytes | None
    mode: int | None = None
    old_display: str | None = None
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
        elif line.startswith("*** Move to: "):
            if current is None or current.operation != "update" or current.body:
                raise ToolError(
                    "INVALID_PATCH",
                    "*** Move to: must immediately follow an Update File header",
                    "validation",
                )
            current.move_to = line.removeprefix("*** Move to: ").strip()
            if not current.move_to:
                raise ToolError("INVALID_PATCH", "Move to path cannot be empty", "validation")
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
    search_order = list(range(start, max(start, len(lines) - len(needle) + 1))) + list(
        range(0, max(0, start))
    )
    matches = [
        index
        for index in search_order
        if lines[index : index + len(needle)] == needle
    ]
    if not matches:
        return -1
    if len(matches) > 1:
        raise ToolError(
            "PATCH_CONTEXT_AMBIGUOUS",
            "update hunk context matched multiple locations; add more unchanged context",
            "conflict",
            True,
            {"match_count": len(matches), "context": "\n".join(needle[:8])},
        )
    return matches[0]


def _updated_text(original: str, body: list[str]) -> tuple[str, int, int]:
    bom = "\ufeff" if original.startswith("\ufeff") else ""
    text = original[1:] if bom else original
    line_ending = "\r\n" if "\r\n" in text and text.find("\r\n") <= text.find("\n") else "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    final_newline = normalized.endswith("\n")
    lines = normalized.splitlines()
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
    if line_ending == "\r\n":
        result = result.replace("\n", "\r\n")
    return bom + result, additions, removals


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
        original_mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
        if section.operation == "add":
            if path.exists():
                raise ToolError("ALREADY_EXISTS", f"cannot add existing file: {section.path}", "conflict")
            output: list[str] = []
            for line in section.body:
                if not line.startswith("+") and line:
                    raise ToolError("INVALID_PATCH", "Add File lines must start with '+'", "validation")
                output.append(line[1:] if line.startswith("+") else "")
            replacement = ("\n".join(output) + ("\n" if section.body else "")).encode("utf-8")
            changes.append(
                PreparedChange(
                    "add",
                    path,
                    resolved.display,
                    None,
                    replacement,
                    0o644,
                    None,
                    len(output),
                    0,
                )
            )
        elif section.operation == "delete":
            if original is None:
                raise ToolError("NOT_FOUND", f"cannot delete missing file: {section.path}", "filesystem")
            changes.append(
                PreparedChange(
                    "delete",
                    path,
                    resolved.display,
                    original,
                    None,
                    original_mode,
                    None,
                    0,
                    len(original.decode("utf-8", "replace").splitlines()),
                )
            )
        else:
            if original is None:
                raise ToolError("NOT_FOUND", f"cannot update missing file: {section.path}", "filesystem")
            try:
                text = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError("NOT_TEXT", f"cannot patch non-UTF-8 file: {section.path}", "validation") from exc
            updated, additions, removals = _updated_text(text, section.body)
            replacement = updated.encode("utf-8")
            if section.move_to:
                destination = workspace.writable(section.move_to)
                if destination.absolute in seen:
                    raise ToolError(
                        "INVALID_PATCH",
                        f"multiple sections target the same file: {section.move_to}",
                        "validation",
                    )
                if destination.absolute.exists() and destination.absolute != path:
                    raise ToolError(
                        "ALREADY_EXISTS",
                        f"cannot move over existing file: {section.move_to}",
                        "conflict",
                    )
                if destination.absolute != path:
                    seen.add(destination.absolute)
                    changes.append(
                        PreparedChange(
                            "move_source",
                            path,
                            resolved.display,
                            original,
                            None,
                            original_mode,
                            None,
                            0,
                            removals,
                        )
                    )
                    changes.append(
                        PreparedChange(
                            "move",
                            destination.absolute,
                            destination.display,
                            None,
                            replacement,
                            original_mode,
                            resolved.display,
                            additions,
                            0,
                        )
                    )
                    continue
            changes.append(
                PreparedChange(
                    "update",
                    path,
                    resolved.display,
                    original,
                    replacement,
                    original_mode,
                    None,
                    additions,
                    removals,
                )
            )
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
                    if change.mode is not None:
                        os.chmod(temp_path, change.mode)
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
                    if change.mode is not None:
                        os.chmod(change.path, change.mode)
            except OSError:
                pass
        raise ToolError("PATCH_COMMIT_FAILED", "patch commit failed; rollback was attempted", "filesystem", True, {"error": str(exc)}) from exc


def apply_patch(workspace: Workspace, patch_text: str, *, dry_run: bool) -> dict[str, object]:
    changes = prepare(workspace, patch_text)
    if not dry_run:
        commit(changes)
    additions = sum(item.additions for item in changes)
    removals = sum(item.removals for item in changes)
    affected_files: list[dict[str, str]] = []
    for item in changes:
        if item.operation == "move_source":
            continue
        affected = {"operation": item.operation, "path": item.display}
        if item.operation == "move" and item.old_display:
            affected["old_path"] = item.old_display
        affected_files.append(affected)
    return {
        "dry_run": dry_run,
        "clean": True,
        "affected_files": affected_files,
        "additions": additions,
        "removals": removals,
        "summary": f"{len(affected_files)} file(s), +{additions} -{removals}",
        "warnings": [],
    }