from __future__ import annotations

from ...core import ToolAnnotations, ToolDefinition
from ...permissions import Capability, OperationPermission
from ...schemas import I, S, obj


SYSTEM_TOOLS = (
    ToolDefinition(
        "server_info",
        "Server info",
        "Return server, workspace, authentication, policy, and tool metadata.",
        obj(),
        "server_info",
        frozenset({Capability.SYSTEM_INSPECT}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "check_exec_environment",
        "Check exec environment",
        "Return the effective command execution environment and safety policy.",
        obj(),
        "check_exec_environment",
        frozenset({Capability.SYSTEM_INSPECT}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "request_permissions",
        "Request permissions",
        "Request an explicit client-side permission confirmation when the client supports MCP elicitation; never silently escalates privileges.",
        obj(
            {
                "tool_name": {
                    **S,
                    "enum": [
                        "discover_toolchains",
                        "exec_process",
                        "exec_command",
                        "apply_patch",
                    ],
                },
                "permission": {
                    **S,
                    "enum": [permission.value for permission in OperationPermission],
                },
                "reason": {**S, "minLength": 1},
                "arguments": {"type": "object", "additionalProperties": True},
                "scope": {**S, "enum": ["once", "session"], "default": "once"},
                "ttl_seconds": {**I, "minimum": 1, "maximum": 3_600, "default": 300},
            },
            ("tool_name", "permission", "reason", "arguments"),
        ),
        "request_permissions",
        frozenset({Capability.PERMISSION_MANAGE}),
        ToolAnnotations(read_only=True),
    ),
)
