"""Stateless MCP JSON-RPC dispatcher.

Legacy clients negotiate through initialize.  The 2026 protocol carries its
version in each request's ``_meta``; no per-client session state is required.
"""

from __future__ import annotations

import base64
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import RpcError, rpc_error_payload


LOGGER = logging.getLogger(__name__)


LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
KNOWN_PROTOCOL_VERSIONS = (*MODERN_PROTOCOL_VERSIONS, *LEGACY_PROTOCOL_VERSIONS)
LATEST_LEGACY_PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSIONS[0]
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
BASE64_SENTINEL_PREFIX = "=?base64?"
BASE64_SENTINEL_SUFFIX = "?="
BASE64_SENTINEL_MAX_PAYLOAD = 8192
MODERN_ERROR_STATUSES = {
    -32601: 404,
    -32602: 400,
    HEADER_MISMATCH: 400,
    UNSUPPORTED_PROTOCOL_VERSION: 400,
}


@dataclass(frozen=True, slots=True)
class RequestContext:
    era: str
    protocol_version: str
    client_info: Mapping[str, Any] | None = None
    client_capabilities: Mapping[str, Any] | None = None
    input_responses: Mapping[str, Any] | None = None
    request_state: str | None = None
    principal: str = ""


ACTIVE_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "agent_runtime_request_context",
    default=None,
)


def current_request_context() -> RequestContext | None:
    return ACTIVE_REQUEST_CONTEXT.get()


def _id(request: dict[str, Any]) -> str | int | None:
    value = request.get("id")
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _validate_rpc_envelope(request: dict[str, Any]) -> None:
    if request.get("jsonrpc") != "2.0":
        raise RpcError(-32600, "Invalid Request: jsonrpc must be 2.0", {"reason": "jsonrpc_version"})
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(-32600, "Invalid Request: method must be a string", {"reason": "method"})
    if "id" in request:
        value = request["id"]
        if not (
            value is None
            or isinstance(value, str)
            or (isinstance(value, int) and not isinstance(value, bool))
        ):
            raise RpcError(-32600, "Invalid Request: id must be string, integer, or null", {"reason": "id"})


def _params(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("params", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RpcError(-32602, "MCP method params must be an object")
    return value


def _modern_context(
    params: dict[str, Any],
    *,
    principal: str = "",
) -> RequestContext | None:
    meta = params.get("_meta")
    if not isinstance(meta, dict) or META_PROTOCOL_VERSION not in meta:
        return None
    version = meta.get(META_PROTOCOL_VERSION)
    if not isinstance(version, str):
        raise RpcError(-32602, f"{META_PROTOCOL_VERSION} must be a string", {"reason": "protocol_version"})
    if version not in MODERN_PROTOCOL_VERSIONS:
        raise RpcError(
            UNSUPPORTED_PROTOCOL_VERSION,
            f"Unsupported MCP protocol version in _meta: {version}",
            {"supported": list(MODERN_PROTOCOL_VERSIONS), "received": version},
        )
    capabilities = meta.get(META_CLIENT_CAPABILITIES)
    if not isinstance(capabilities, dict):
        raise RpcError(
            -32602,
            f"{META_CLIENT_CAPABILITIES} is required and must be an object",
            {"reason": "client_capabilities"},
        )
    raw_info = meta.get(META_CLIENT_INFO)
    if META_CLIENT_INFO in meta and not isinstance(raw_info, dict):
        raise RpcError(
            -32602,
            f"{META_CLIENT_INFO} must be an object when present",
            {"reason": "client_info"},
        )
    info: dict[str, str] | None = None
    if isinstance(raw_info, dict):
        info = {}
        for key in ("name", "version"):
            value = raw_info.get(key)
            if isinstance(value, str):
                info[key] = value[:200]
    raw_input_responses = params.get("inputResponses")
    if raw_input_responses is not None and not isinstance(raw_input_responses, dict):
        raise RpcError(
            -32602,
            "inputResponses must be an object when present",
            {"reason": "input_responses"},
        )
    raw_request_state = params.get("requestState")
    if raw_request_state is not None and not isinstance(raw_request_state, str):
        raise RpcError(
            -32602,
            "requestState must be a string when present",
            {"reason": "request_state"},
        )
    return RequestContext(
        "modern",
        str(version),
        info,
        capabilities,
        raw_input_responses,
        raw_request_state,
        principal,
    )


def _shape_modern(method: str, result: dict[str, Any], runtime: Any) -> dict[str, Any]:
    shaped = dict(result)
    shaped.setdefault("resultType", "complete")
    raw_meta = shaped.get("_meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta[META_SERVER_INFO] = runtime.server_identity()
    shaped["_meta"] = meta
    if method in {"server/discover", "tools/list"}:
        shaped["ttlMs"] = 0
        shaped["cacheScope"] = "private"
    return shaped


def decode_mirror_header(value: str) -> str:
    if not (value.startswith(BASE64_SENTINEL_PREFIX) and value.endswith(BASE64_SENTINEL_SUFFIX)):
        return value
    payload = value[len(BASE64_SENTINEL_PREFIX) : -len(BASE64_SENTINEL_SUFFIX)]
    if len(payload) > BASE64_SENTINEL_MAX_PAYLOAD:
        raise RpcError(
            HEADER_MISMATCH,
            "Mirror header carries an oversized base64 sentinel",
            {"reason": "oversized", "max_length": BASE64_SENTINEL_MAX_PAYLOAD},
        )
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RpcError(
            HEADER_MISMATCH,
            "Mirror header carries a base64 sentinel that does not decode to UTF-8",
            {"reason": "invalid_base64"},
        ) from exc


def validate_mirror_headers(
    method: str,
    params: Mapping[str, Any],
    *,
    version_header: str | None,
    method_header: str | None,
    name_header: str | None,
) -> None:
    modern = _modern_context(dict(params))
    if modern is None:
        if version_header in MODERN_PROTOCOL_VERSIONS:
            raise RpcError(
                HEADER_MISMATCH,
                "MCP-Protocol-Version requires the same modern version in params._meta",
                {"header": "MCP-Protocol-Version", "reason": "body_is_not_modern"},
            )
        return
    if version_header is None or version_header != modern.protocol_version:
        raise RpcError(
            HEADER_MISMATCH,
            "MCP-Protocol-Version is required and must match params._meta",
            {"header": "MCP-Protocol-Version", "reason": "missing" if version_header is None else "mismatch"},
        )
    if method_header is None or method_header != method:
        raise RpcError(
            HEADER_MISMATCH,
            "Mcp-Method is required and must match the request method",
            {"header": "Mcp-Method", "reason": "missing" if method_header is None else "mismatch"},
        )
    subject_key = {"tools/call": "name", "resources/read": "uri"}.get(method)
    if subject_key is None:
        return
    if name_header is None or decode_mirror_header(name_header) != params.get(subject_key):
        raise RpcError(
            HEADER_MISMATCH,
            f"Mcp-Name is required for {method} and must match params.{subject_key}",
            {"header": "Mcp-Name", "reason": "missing" if name_header is None else "mismatch"},
        )


def rpc_response_status(request: dict[str, Any], response: dict[str, Any]) -> int:
    try:
        params = _params(request)
        modern = _modern_context(params)
    except RpcError:
        modern = None
    if modern is None:
        return 200
    error = response.get("error")
    if not isinstance(error, dict):
        return 200
    return MODERN_ERROR_STATUSES.get(error.get("code"), 200)


def dispatch(
    runtime: Any,
    request: dict[str, Any],
    *,
    transport_protocol_version: str | None = None,
    principal: str = "",
) -> dict[str, Any] | None:
    request_id = _id(request)
    is_notification = "id" not in request
    try:
        _validate_rpc_envelope(request)
        method = request["method"]
        params = _params(request)
        modern = _modern_context(params, principal=principal)
        if modern is not None:
            result = _dispatch_modern(runtime, method, params, modern)
            if result is None or is_notification:
                return None
            result = _shape_modern(method, result, runtime)
        else:
            result = _dispatch_legacy(
                runtime,
                request,
                method,
                params,
                transport_protocol_version,
                principal,
            )
            if result is None or is_notification:
                return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as exc:
        if is_notification:
            return None
        return rpc_error_payload(request_id, exc)
    except Exception as exc:
        # JSON-RPC requests must fail as JSON-RPC responses, not as broken
        # HTTP transports. This is the final boundary for unexpected protocol
        # or runtime failures that occur outside Runtime.call_tool().
        LOGGER.exception("Unexpected MCP dispatch failure for request %r", request_id)
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32603,
                "message": "Internal error",
                "data": {"exception_type": type(exc).__name__},
            },
        }


def _dispatch_modern(runtime: Any, method: str, params: dict[str, Any], context: RequestContext) -> dict[str, Any] | None:
    if method == "server/discover":
        return {
            "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
            "capabilities": {
                "tools": {"listChanged": False},
            },
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


def _dispatch_legacy(
    runtime: Any,
    request: dict[str, Any],
    method: str,
    params: dict[str, Any],
    transport_protocol_version: str | None,
    principal: str,
) -> dict[str, Any] | None:
    if method == "initialize":
        if request.get("id") is None:
            raise RpcError(-32600, "initialize must be a JSON-RPC request with a non-null id")
        requested = params.get("protocolVersion")
        if requested not in LEGACY_PROTOCOL_VERSIONS:
            requested = LATEST_LEGACY_PROTOCOL_VERSION
        return {
            "protocolVersion": requested,
            "capabilities": {
                "tools": {"listChanged": False},
            },
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
        version = transport_protocol_version if transport_protocol_version in LEGACY_PROTOCOL_VERSIONS else LATEST_LEGACY_PROTOCOL_VERSION
        return _tool_call(
            runtime,
            params,
            RequestContext("legacy", version, principal=principal),
        )
    raise RpcError(-32601, f"Unknown method: {method}")


def _tool_call(runtime: Any, params: dict[str, Any], context: RequestContext) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        raise RpcError(-32602, "tools/call requires a tool name")
    if not isinstance(arguments, dict):
        raise RpcError(-32602, "tools/call arguments must be an object")
    return runtime.call_tool(name, arguments, context=context)

