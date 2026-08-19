from __future__ import annotations

from ...core import ToolAnnotations, ToolDefinition
from ...permissions import Capability
from ...schemas import B, EXEC_COMMON, I, S, obj


PROCESS_TOOLS = (
    ToolDefinition(
        "exec_process",
        "Execute process",
        "Run a structured process without accepting a user-provided shell command string.",
        obj({"program": {**S, "minLength": 1}, "args": {"type": "array", "items": S, "default": []}, "workdir": {**S, "default": "."}, "cwd": S, "timeout_ms": {**I, "minimum": 1, "maximum": 600_000, "default": 30_000}, "yield_time_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 10_000}, "stdin": {**S, "default": ""}, "tty": {**B, "default": False}, "env": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}}, **EXEC_COMMON}, ("program",)),
        "exec_process",
        frozenset({Capability.PROCESS_EXECUTE}),
        ToolAnnotations(destructive=True, open_world=True),
    ),
    ToolDefinition(
        "exec_command",
        "Execute command",
        "Run a bounded command under the configured execution policy.",
        obj({"cmd": {**S, "minLength": 1}, "workdir": {**S, "default": "."}, "cwd": S, "timeout_ms": {**I, "minimum": 1, "maximum": 600_000, "default": 30_000}, "yield_time_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 10_000}, "stdin": {**S, "default": ""}, "tty": {**B, "default": False}, "env": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}}, **EXEC_COMMON}, ("cmd",)),
        "exec_command",
        frozenset({Capability.PROCESS_EXECUTE}),
        ToolAnnotations(destructive=True, open_world=True),
    ),
    ToolDefinition(
        "write_stdin",
        "Write stdin",
        "Write input to, or poll, a running server-managed command.",
        obj({"command_id": {**S, "minLength": 1}, "chars": {**S, "default": ""}, "yield_time_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 10_000}, **EXEC_COMMON}, ("command_id",)),
        "write_stdin",
        frozenset({Capability.PROCESS_CONTROL}),
    ),
    ToolDefinition(
        "kill_command",
        "Kill command",
        "Terminate a server-managed command by command id.",
        obj({"command_id": {**S, "minLength": 1}, "signal": {**S, "enum": ["TERM", "KILL", "INT"], "default": "TERM"}, "wait_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 5_000}, "kill_wait_ms": {**I, "minimum": 0, "maximum": 30_000, "default": 2_000}, **EXEC_COMMON}, ("command_id",)),
        "kill_command",
        frozenset({Capability.PROCESS_CONTROL}),
        ToolAnnotations(destructive=True),
    ),
    ToolDefinition(
        "read_output",
        "Read output",
        "Read retained stdout/stderr from a managed command.",
        obj({"output_ref": {**S, "minLength": 1}, "stream": {**S, "enum": ["stdout", "stderr"]}, "offset": {**I, "minimum": 0, "default": 0}, "limit": {**I, "minimum": 1, "maximum": 1_048_576, "default": 4_096}}, ("output_ref",)),
        "read_output",
        frozenset({Capability.PROCESS_CONTROL}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
)
