"""Newline-delimited JSON-RPC transport for local MCP clients."""

from __future__ import annotations

import json
import sys
from typing import Any

from .protocol import dispatch


def serve_stdio(runtime: Any) -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = dispatch(runtime, request)
        except (ValueError, json.JSONDecodeError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0