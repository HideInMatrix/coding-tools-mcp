from __future__ import annotations

from ...core.tool import ToolAnnotations, ToolDefinition
from ...permissions.capabilities import Capability
from ...schemas import B, I, S, SA, obj


_READ_ONLY = ToolAnnotations(read_only=True, idempotent=True)

GIT_TOOLS = (
    ToolDefinition("git_status", "Git status", "Return structured git working-tree status.", obj({"path": {**S, "default": "."}, "include_untracked": {**B, "default": True}, "max_entries": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}}), "git_status", frozenset({Capability.GIT_READ}), _READ_ONLY, mcp_exposed=False),
    ToolDefinition("git_diff", "Git diff", "Return a bounded unified git diff.", obj({"path": S, "paths": SA, "staged": {**B, "default": False}, "unstaged": {**B, "default": True}, "context_lines": {**I, "minimum": 0, "maximum": 20, "default": 3}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 262_144}}), "git_diff", frozenset({Capability.GIT_READ}), _READ_ONLY, mcp_exposed=False),
    ToolDefinition("git_log", "Git log", "Return recent git commits with bounded structured metadata.", obj({"path": {**S, "default": "."}, "ref": {**S, "default": "HEAD"}, "max_count": {**I, "minimum": 1, "maximum": 100, "default": 20}, "skip": {**I, "minimum": 0, "maximum": 10_000, "default": 0}}), "git_log", frozenset({Capability.GIT_READ}), _READ_ONLY, mcp_exposed=False),
    ToolDefinition("git_show", "Git show", "Return bounded git show output for a revision.", obj({"rev": {**S, "default": "HEAD"}, "path": S, "paths": SA, "include_diff": {**B, "default": True}, "context_lines": {**I, "minimum": 0, "maximum": 20, "default": 3}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 262_144}}), "git_show", frozenset({Capability.GIT_READ}), _READ_ONLY, mcp_exposed=False),
    ToolDefinition("git_blame", "Git blame", "Return bounded git blame metadata for a workspace file.", obj({"path": {**S, "minLength": 1}, "rev": S, "start_line": {**I, "minimum": 1, "default": 1}, "end_line": {**I, "minimum": 1}, "max_lines": {**I, "minimum": 1, "maximum": 1_000, "default": 200}}, ("path",)), "git_blame", frozenset({Capability.GIT_READ}), _READ_ONLY, mcp_exposed=False),
    ToolDefinition(
        "git_inspect",
        "Git inspect",
        "Inspect Git state through one domain tool. action=status|diff|log|show|blame; pass the same fields accepted by the corresponding internal Git operation.",
        obj(
            {
                "action": {**S, "enum": ["status", "diff", "log", "show", "blame"]},
                "path": S,
                "paths": SA,
                "include_untracked": B,
                "max_entries": I,
                "staged": B,
                "unstaged": B,
                "context_lines": I,
                "max_bytes": I,
                "ref": S,
                "max_count": I,
                "skip": I,
                "rev": S,
                "include_diff": B,
                "start_line": I,
                "end_line": I,
                "max_lines": I,
            },
            ("action",),
        ),
        "git_inspect",
        frozenset({Capability.GIT_READ}),
        _READ_ONLY,
    ),
)
