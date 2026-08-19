from __future__ import annotations

import contextvars


ACTIVE_PERMISSIONS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "coding_tools_mcp_active_permissions",
    default=frozenset(),
)


__all__ = ["ACTIVE_PERMISSIONS"]
