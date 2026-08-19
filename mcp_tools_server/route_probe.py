from __future__ import annotations

import hashlib
from pathlib import Path


ROUTE_PROBE_TOKEN_ENV = "CODING_TOOLS_MCP_ROUTE_PROBE_TOKEN"
ROUTE_PROBE_HEADER = "X-Coding-Tools-MCP-Route-Probe"
ROUTE_PROBE_PATH = "/.well-known/coding-tools-mcp-route-probe"


def workspace_fingerprint(path: Path) -> str:
    """Return a non-reversible diagnostic identity for one Runtime workspace."""

    normalized = str(path.expanduser().resolve()).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:24]

