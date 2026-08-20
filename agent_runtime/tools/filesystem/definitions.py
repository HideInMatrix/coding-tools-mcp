from __future__ import annotations

from ...core.tool import ToolAnnotations, ToolDefinition
from ...permissions.capabilities import Capability
from ...schemas import B, I, S, SA, obj


FILESYSTEM_TOOLS = (
    ToolDefinition(
        "read_file",
        "Read file",
        "Read a UTF-8 text file slice inside the configured workspace.",
        obj({"path": {**S, "minLength": 1}, "start_line": {**I, "minimum": 1, "default": 1}, "end_line": {**I, "minimum": 1}, "max_lines": {**I, "minimum": 1}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 131_072}, "encoding": {**S, "enum": ["utf-8"], "default": "utf-8"}}, ("path",)),
        "read_file",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "list_dir",
        "List directory",
        "List directory entries inside the configured workspace.",
        obj({"path": {**S, "default": "."}, "recursive": {**B, "default": False}, "max_depth": {**I, "minimum": 1, "maximum": 20, "default": 1}, "max_entries": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}, "include_hidden": {**B, "default": False}, "include_ignored": {**B, "default": False}, "sort": {**S, "enum": ["name", "type", "modified"], "default": "name"}}),
        "list_dir",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "list_files",
        "List files",
        "List workspace files using glob filters.",
        obj({"path": {**S, "default": "."}, "patterns": SA, "glob": S, "exclude_patterns": SA, "include_hidden": {**B, "default": False}, "include_ignored": {**B, "default": False}, "max_results": {**I, "minimum": 1, "maximum": 50_000, "default": 5_000}, "sort": {**S, "enum": ["path", "modified"], "default": "path"}}),
        "list_files",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "search_text",
        "Search text",
        "Search UTF-8 workspace files for literal or regular-expression matches.",
        obj({"query": {**S, "minLength": 1}, "path": {**S, "default": "."}, "regex": {**B, "default": False}, "case_sensitive": {**B, "default": False}, "include_globs": SA, "glob": S, "exclude_globs": SA, "context_lines": {**I, "minimum": 0, "maximum": 5, "default": 0}, "max_results": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}, "max_preview_bytes": {**I, "minimum": 80, "maximum": 4_096, "default": 512}}, ("query",)),
        "search_text",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "apply_patch",
        "Apply patch",
        "Validate and atomically apply a Begin Patch envelope.",
        obj({"patch": {**S, "minLength": 1}, "dry_run": {**B, "default": False}}, ("patch",)),
        "apply_patch",
        frozenset({Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True),
    ),
    ToolDefinition(
        "view_image",
        "View image",
        "Return a workspace image as MCP image content.",
        obj({"path": {**S, "minLength": 1}, "max_bytes": {**I, "minimum": 1_024, "maximum": 10_485_760, "default": 5_242_880}, "max_width": {**I, "minimum": 1, "maximum": 10_000, "default": 2_000}, "max_height": {**I, "minimum": 1, "maximum": 10_000, "default": 2_000}, "auto_resize": {**B, "default": True}}, ("path",)),
        "view_image",
        frozenset({Capability.FILESYSTEM_READ, Capability.MEDIA_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
        feature="view_image",
    ),
)
