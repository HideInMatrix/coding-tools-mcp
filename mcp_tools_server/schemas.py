"""MCP tool catalogue and JSON schemas.

Every tool exposes both inputSchema and outputSchema.  The latter is important
for clients such as ChatGPT because it makes completion/error state explicit
instead of forcing the model to infer it from prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False

    def definition(self, *, fake_readonly: bool = False) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": output_schema(),
            "annotations": {
                "title": self.title,
                "readOnlyHint": True if fake_readonly else self.read_only,
                "destructiveHint": False if fake_readonly else self.destructive,
                "idempotentHint": self.idempotent,
                "openWorldHint": False if fake_readonly else self.open_world,
            },
        }


def obj(
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
    *,
    additional: bool | dict[str, Any] = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": additional,
    }


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
SA = {"type": "array", "items": {"type": "string"}}


def output_schema() -> dict[str, Any]:
    return obj(
        {
            "ok": {"type": "boolean"},
            "error": obj(
                {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "category": {"type": "string"},
                    "retryable": {"type": "boolean"},
                    "details": {"type": "object", "additionalProperties": True},
                },
                ("code", "message", "category", "retryable", "details"),
                additional=True,
            ),
        },
        ("ok",),
        additional=True,
    )


EXEC_COMMON = {
    "max_output_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 65_536},
    "verbosity": {**S, "enum": ["summary", "preview", "full"]},
    "preview_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 4_096},
}


TOOL_SPECS: dict[str, ToolSpec] = {
    "server_info": ToolSpec("server_info", "Server info", "Return server, workspace, authentication, policy, and tool metadata.", obj(), True, False, True),
    "check_exec_environment": ToolSpec("check_exec_environment", "Check exec environment", "Return the effective command execution environment and safety policy.", obj(), True, False, True),
    "read_file": ToolSpec("read_file", "Read file", "Read a UTF-8 text file slice inside the configured workspace.", obj({"path": {**S, "minLength": 1}, "start_line": {**I, "minimum": 1, "default": 1}, "end_line": {**I, "minimum": 1}, "max_lines": {**I, "minimum": 1}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 131_072}, "encoding": {**S, "enum": ["utf-8"], "default": "utf-8"}}, ("path",)), True, False, True),
    "list_dir": ToolSpec("list_dir", "List directory", "List directory entries inside the configured workspace.", obj({"path": {**S, "default": "."}, "recursive": {**B, "default": False}, "max_depth": {**I, "minimum": 1, "maximum": 20, "default": 1}, "max_entries": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}, "include_hidden": {**B, "default": False}, "include_ignored": {**B, "default": False}, "sort": {**S, "enum": ["name", "type", "modified"], "default": "name"}}), True, False, True),
    "list_files": ToolSpec("list_files", "List files", "List workspace files using glob filters.", obj({"path": {**S, "default": "."}, "patterns": SA, "glob": S, "exclude_patterns": SA, "include_hidden": {**B, "default": False}, "include_ignored": {**B, "default": False}, "max_results": {**I, "minimum": 1, "maximum": 50_000, "default": 5_000}, "sort": {**S, "enum": ["path", "modified"], "default": "path"}}), True, False, True),
    "search_text": ToolSpec("search_text", "Search text", "Search UTF-8 workspace files for literal or regular-expression matches.", obj({"query": {**S, "minLength": 1}, "path": {**S, "default": "."}, "regex": {**B, "default": False}, "case_sensitive": {**B, "default": False}, "include_globs": SA, "glob": S, "exclude_globs": SA, "context_lines": {**I, "minimum": 0, "maximum": 5, "default": 0}, "max_results": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}, "max_preview_bytes": {**I, "minimum": 80, "maximum": 4_096, "default": 512}}, ("query",)), True, False, True),
    "apply_patch": ToolSpec("apply_patch", "Apply patch", "Validate and atomically apply a Begin Patch envelope.", obj({"patch": {**S, "minLength": 1}, "dry_run": {**B, "default": False}}, ("patch",)), False, True),
    "exec_command": ToolSpec("exec_command", "Execute command", "Run a bounded command under the configured execution policy.", obj({"cmd": {**S, "minLength": 1}, "workdir": {**S, "default": "."}, "cwd": S, "timeout_ms": {**I, "minimum": 1, "maximum": 600_000, "default": 30_000}, "yield_time_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 10_000}, "stdin": {**S, "default": ""}, "tty": {**B, "default": False}, "env": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}}, **EXEC_COMMON}, ("cmd",)), False, True, False, True),
    "write_stdin": ToolSpec("write_stdin", "Write stdin", "Write input to, or poll, a running server-managed command.", obj({"command_id": {**S, "minLength": 1}, "chars": {**S, "default": ""}, "yield_time_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 10_000}, **EXEC_COMMON}, ("command_id",))),
    "kill_command": ToolSpec("kill_command", "Kill command", "Terminate a server-managed command by command id.", obj({"command_id": {**S, "minLength": 1}, "signal": {**S, "enum": ["TERM", "KILL", "INT"], "default": "TERM"}, "wait_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 5_000}, "kill_wait_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 2_000}, **EXEC_COMMON}, ("command_id",)), False, True),
    "read_output": ToolSpec("read_output", "Read output", "Read retained stdout/stderr from a managed command.", obj({"output_ref": {**S, "minLength": 1}, "stream": {**S, "enum": ["stdout", "stderr"]}, "offset": {**I, "minimum": 0, "default": 0}, "limit": {**I, "minimum": 1, "maximum": 1_048_576, "default": 4_096}}, ("output_ref",)), True, False, True),
    "git_status": ToolSpec("git_status", "Git status", "Return structured git working-tree status.", obj({"path": {**S, "default": "."}, "include_untracked": {**B, "default": True}, "max_entries": {**I, "minimum": 1, "maximum": 10_000, "default": 1_000}}), True, False, True),
    "git_diff": ToolSpec("git_diff", "Git diff", "Return a bounded unified git diff.", obj({"path": S, "paths": SA, "staged": {**B, "default": False}, "unstaged": {**B, "default": True}, "context_lines": {**I, "minimum": 0, "maximum": 20, "default": 3}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 262_144}}), True, False, True),
    "git_log": ToolSpec("git_log", "Git log", "Return recent git commits with bounded structured metadata.", obj({"path": {**S, "default": "."}, "ref": {**S, "default": "HEAD"}, "max_count": {**I, "minimum": 1, "maximum": 100, "default": 20}, "skip": {**I, "minimum": 0, "maximum": 10_000, "default": 0}}), True, False, True),
    "git_show": ToolSpec("git_show", "Git show", "Return bounded git show output for a revision.", obj({"rev": {**S, "default": "HEAD"}, "path": S, "paths": SA, "include_diff": {**B, "default": True}, "context_lines": {**I, "minimum": 0, "maximum": 20, "default": 3}, "max_bytes": {**I, "minimum": 1, "maximum": 1_048_576, "default": 262_144}}), True, False, True),
    "git_blame": ToolSpec("git_blame", "Git blame", "Return bounded git blame metadata for a workspace file.", obj({"path": {**S, "minLength": 1}, "rev": S, "start_line": {**I, "minimum": 1, "default": 1}, "end_line": {**I, "minimum": 1}, "max_lines": {**I, "minimum": 1, "maximum": 1_000, "default": 200}}, ("path",)), True, False, True),
    "request_permissions": ToolSpec("request_permissions", "Request permissions", "Report whether an operation is allowed; never silently escalates privileges.", obj({"tool_name": {**S, "enum": ["exec_command", "apply_patch"]}, "permission": {**S, "enum": ["network", "destructive_command", "long_timeout", "sensitive_env", "shell_expansion", "inline_script", "privileged_executable", "write_generated_or_ignored"]}, "reason": {**S, "minLength": 1}, "arguments": {"type": "object", "additionalProperties": True}, "scope": {**S, "enum": ["once", "session"], "default": "once"}, "ttl_seconds": {**I, "minimum": 1, "maximum": 3_600, "default": 300}}, ("tool_name", "permission", "reason", "arguments")), True),
    "view_image": ToolSpec("view_image", "View image", "Return a workspace image as MCP image content.", obj({"path": {**S, "minLength": 1}, "max_bytes": {**I, "minimum": 1_024, "maximum": 10_485_760, "default": 5_242_880}, "max_width": {**I, "minimum": 1, "maximum": 10_000, "default": 2_000}, "max_height": {**I, "minimum": 1, "maximum": 10_000, "default": 2_000}, "auto_resize": {**B, "default": True}}, ("path",)), True, False, True),
}


def exposed_specs(*, enable_view_image: bool = True) -> list[ToolSpec]:
    return [spec for name, spec in TOOL_SPECS.items() if enable_view_image or name != "view_image"]


def validate_value(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = props.get(key)
            if child is None:
                if additional is False:
                    raise ValueError(f"{path}.{key} is not allowed")
                if isinstance(additional, dict):
                    validate_value(item, additional, f"{path}.{key}")
            else:
                validate_value(item, child, f"{path}.{key}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        child = schema.get("items")
        if isinstance(child, dict):
            for index, item in enumerate(value):
                validate_value(item, child, f"{path}[{index}]")
        return
    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if len(value) < int(schema.get("minLength", 0)):
            raise ValueError(f"{path} is too short")
    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{path} must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} is above the maximum")
    elif expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
