from __future__ import annotations

from ...core.tool import ToolAnnotations, ToolDefinition
from ...permissions.capabilities import Capability
from ...schemas import S, obj


TOOLCHAIN_TOOLS = (
    ToolDefinition(
        "discover_toolchains",
        "Discover toolchains",
        "Discover validated Node.js, Python, and Go toolchains in the sandbox first; if absent, request permission before querying the user environment.",
        obj(
            {
                "kinds": {
                    "type": "array",
                    "items": {**S, "enum": ["node", "python", "go"]},
                    "default": ["node", "python", "go"],
                }
            }
        ),
        "discover_toolchains",
        frozenset({Capability.TOOLCHAIN_DISCOVER}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
)
