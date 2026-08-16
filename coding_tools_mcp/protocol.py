"""Stateless MCP JSON-RPC dispatcher.

Legacy clients negotiate through initialize.  The 2026 protocol carries its
version in each request's ``_meta``; no per-client session state is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import RpcError, rpc_error_payload


LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
KNOWN_PROTOCOL_VERSIONS = (*MODERN_PROTOCOL_VERSIONS, *LEGACY_PROTOCOL_VERSIONS)
LATEST_LEGACY_PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSIONS[0]
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"


@dataclass(frozen=True, slots=True)
class RequestContext:
    era: str
    protocol_version: str
    client_info: Mapping[str, Any] | None = None


def _id(request: dict[str, Any]) -> str | int | None:
    value = request.get("id")
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _params(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("params", {})
    if not isinstance(value, dict):
        raise RpcError(-32602, "params must be an object")
    return value


def _modern_context(params: dict[str, Any]) -> RequestContext | None:
    meta = params.get("_meta")
    if not isinstance(meta, dict) or META_PROTOCOL_VERSION not in meta:
        return None
    version = meta.get(META_PROTOCOL_VERSION)
    if version not in MODERN_PROTOCOL_VERSIONS:
        raise RpcError(-32022, f"Unsupported MCP protocol version: {version}", {"supported": list(MODERN_PROTOCOL_VERSIONS)})
    capabilities = meta.get(META_CLIENT_CAPABILITIES, {})
    if not isinstance(capabilities, dict):
        raise RpcError(-32602, f"{META_CLIENT_CAPABILITIES} must be an object")
    raw_info = meta.get(META_CLIENT_INFO)
    info: dict[str, str] | None = None
    if isinstance(raw_info, dict):
        info = {}
        for key in ("name", "version"):
            value = raw_info.get(key)
            if isinstance(value, str):
                info[key] = value[:200]
    return RequestContext("modern", str(version), info)


def _shape_modern(method: str, result: dict[str, Any], runtime: Any) -> dict[str, Any]:
    shaped = dict(result)
    shaped["resultType"] = "complete"
    raw_meta = shaped.get("_meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta[META_SERVER_INFO] = runtime.server_identity()
    shaped["_meta"] = meta
    if method in {"server/discover", "tools/list"}:
        shaped["ttlMs"] = 0
        shaped["cacheScope"] = "private"
    return shaped


def dispatch(runtime: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = _id(request)
    is_notification = "id" not in request
    try:
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            raise RpcError(-32600, "Invalid Request")
        method = request["method"]
        params = _params(request)
        modern = _modern_context(params)
        if modern is not None:
            result = _dispatch_modern(runtime, method, params, modern)
            if result is None or is_notification:
                return None
            result = _shape_modern(method, result, runtime)
        else:
            result = _dispatch_legacy(runtime, method, params)
            if result is None or is_notification:
                return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as exc:
        if is_notification:
            return None
        return rpc_error_payload(request_id, exc)


def _dispatch_modern(runtime: Any, method: str, params: dict[str, Any], context: RequestContext) -> dict[str, Any] | None:
    if method == "server/discover":
        return {
            "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": runtime.project_context.server_instructions(),
        }
    if method == "ping":
        return {}
    if method in {"notifications/cancelled", "notifications/initialized"}:
        return None
    if method == "tools/list":
        return runtime.list_tools()
    if method == "tools/call":
        return _tool_call(runtime, params, context)
    raise RpcError(-32601, f"Unknown method: {method}")


def _dispatch_legacy(runtime: Any, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if method == "initialize":
        requested = params.get("protocolVersion")
        if requested not in LEGACY_PROTOCOL_VERSIONS:
            requested = LATEST_LEGACY_PROTOCOL_VERSION
        return {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": runtime.server_identity(),
            "instructions": runtime.project_context.server_instructions(),
        }
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return runtime.list_tools()
    if method == "tools/call":
        return _tool_call(runtime, params, RequestContext("legacy", LATEST_LEGACY_PROTOCOL_VERSION))
    raise RpcError(-32601, f"Unknown method: {method}")


def _tool_call(runtime: Any, params: dict[str, Any], context: RequestContext) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        raise RpcError(-32602, "tools/call requires a tool name")
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "tools/call arguments must be an object")
    return runtime.call_tool(name, arguments, context=context)
