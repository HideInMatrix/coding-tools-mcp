from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .mcp_connections import DiscoveredMCPTool, MCPConnectionDefinition


MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"


@dataclass(frozen=True, slots=True)
class MCPConnectionProbe:
    ok: bool
    protocol_version: str = ""
    tools: tuple[DiscoveredMCPTool, ...] = ()
    error: str = ""
    elapsed_ms: int = 0


def _resolve_ref(reference: str) -> str:
    value = reference.strip()
    if value.startswith("env:"):
        name = value[4:].strip()
        if not name:
            raise ValueError("empty env secret reference")
        resolved = os.environ.get(name)
        if resolved is None:
            raise ValueError(f"secret reference is not available: env:{name}")
        return resolved
    raise ValueError(f"unsupported secret reference: {reference!r}; currently use env:NAME")


def resolved_environment(definition: MCPConnectionDefinition) -> dict[str, str]:
    result = dict(definition.environment)
    for key, reference in definition.environment_refs.items():
        result[key] = _resolve_ref(reference)
    return result


def resolved_headers(definition: MCPConnectionDefinition) -> dict[str, str]:
    result = dict(definition.headers)
    for key, reference in definition.header_refs.items():
        result[key] = _resolve_ref(reference)
    return result


def _modern_params() -> dict[str, Any]:
    return {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "micromatrix-workbench",
                "version": "1",
            },
        }
    }


def _request(method: str, params: Mapping[str, Any], request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": dict(params),
    }


def _result(response: Mapping[str, Any]) -> dict[str, Any]:
    error = response.get("error")
    if isinstance(error, Mapping):
        raise RuntimeError(str(error.get("message") or "MCP request failed"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("MCP response does not contain an object result")
    return result


def _tools_from_result(result: Mapping[str, Any]) -> tuple[DiscoveredMCPTool, ...]:
    raw_tools = result.get("tools", [])
    if not isinstance(raw_tools, list):
        raise RuntimeError("MCP tools/list result.tools must be an array")
    tools: list[DiscoveredMCPTool] = []
    for item in raw_tools:
        if not isinstance(item, Mapping):
            continue
        tools.append(DiscoveredMCPTool.from_mapping(item))
    return tuple(sorted(tools, key=lambda item: item.name))


def _http_rpc(
    definition: MCPConnectionDefinition,
    request: dict[str, Any],
    *,
    modern: bool,
    timeout: float,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **resolved_headers(definition),
    }
    method = str(request.get("method") or "")
    if modern:
        headers["MCP-Protocol-Version"] = MODERN_PROTOCOL_VERSION
        headers["Mcp-Method"] = method
        if method == "tools/call":
            raw_params = request.get("params")
            if isinstance(raw_params, Mapping):
                tool_name = str(raw_params.get("name") or "").strip()
                if tool_name:
                    headers["Mcp-Name"] = tool_name
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")
    raw_request = urllib.request.Request(
        definition.endpoint,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(raw_request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            payload = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(8192).decode("utf-8", errors="replace")
        finally:
            exc.close()
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MCP HTTP connection failed: {exc.reason}") from exc
    if "text/event-stream" in content_type:
        for line in payload.decode("utf-8", errors="replace").splitlines():
            if line.startswith("data:"):
                value = json.loads(line[5:].strip())
                if isinstance(value, dict) and value.get("id") == request.get("id"):
                    return value
        raise RuntimeError("MCP SSE response did not contain the requested JSON-RPC result")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("MCP HTTP response must be a JSON object")
    return value


def _stdio_roundtrip(
    definition: MCPConnectionDefinition,
    requests: list[dict[str, Any]],
    *,
    timeout: float,
) -> list[dict[str, Any]]:
    environment = os.environ.copy()
    environment.update(resolved_environment(definition))
    process = subprocess.Popen(
        [definition.command, *definition.arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=environment,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    responses: list[dict[str, Any]] = []
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_stdout() -> None:
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + timeout

        def send(request: dict[str, Any]) -> None:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()

        def wait_for(request_id: Any) -> dict[str, Any]:
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    line = output_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if line is None:
                    break
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("id") == request_id:
                    return value
            stderr = (
                process.stderr.read(4096)
                if process.stderr is not None and process.poll() is not None
                else ""
            )
            raise RuntimeError(
                f"MCP stdio request timed out or server exited: {stderr.strip()}"
            )

        for index, request in enumerate(requests):
            send(request)
            request_id = request.get("id")
            if request_id is not None:
                responses.append(wait_for(request_id))
            if index == 0 and request.get("method") == "initialize":
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return responses
    finally:
        try:
            process.terminate()
            process.wait(timeout=1)
        except Exception:
            process.kill()
            try:
                process.wait(timeout=1)
            except Exception:
                pass
        reader.join(timeout=0.5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def probe_connection(
    definition: MCPConnectionDefinition,
    *,
    discover_tools: bool,
    timeout: float = 8.0,
) -> MCPConnectionProbe:
    started = time.monotonic()
    try:
        if definition.transport == "http":
            modern_discover = _request("server/discover", _modern_params(), 1)
            try:
                discovered = _result(
                    _http_rpc(definition, modern_discover, modern=True, timeout=timeout)
                )
                protocol = MODERN_PROTOCOL_VERSION
                tools: tuple[DiscoveredMCPTool, ...] = ()
                if discover_tools:
                    tools_result = _result(
                        _http_rpc(
                            definition,
                            _request("tools/list", _modern_params(), 2),
                            modern=True,
                            timeout=timeout,
                        )
                    )
                    tools = _tools_from_result(tools_result)
                return MCPConnectionProbe(
                    True,
                    protocol_version=protocol,
                    tools=tools,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception:
                initialize = _request(
                    "initialize",
                    {
                        "protocolVersion": LEGACY_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "micromatrix-workbench", "version": "1"},
                    },
                    1,
                )
                initialized = _result(
                    _http_rpc(definition, initialize, modern=False, timeout=timeout)
                )
                protocol = str(initialized.get("protocolVersion") or LEGACY_PROTOCOL_VERSION)
                tools = ()
                if discover_tools:
                    tools = _tools_from_result(
                        _result(
                            _http_rpc(
                                definition,
                                _request("tools/list", {}, 2),
                                modern=False,
                                timeout=timeout,
                            )
                        )
                    )
                return MCPConnectionProbe(
                    True,
                    protocol_version=protocol,
                    tools=tools,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )

        initialize = _request(
            "initialize",
            {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "micromatrix-workbench", "version": "1"},
            },
            1,
        )
        requests = [initialize]
        if discover_tools:
            requests.append(_request("tools/list", {}, 2))
        responses = _stdio_roundtrip(definition, requests, timeout=timeout)
        by_id = {item.get("id"): item for item in responses}
        initialized = _result(by_id[1])
        tools = _tools_from_result(_result(by_id[2])) if discover_tools else ()
        return MCPConnectionProbe(
            True,
            protocol_version=str(initialized.get("protocolVersion") or LEGACY_PROTOCOL_VERSION),
            tools=tools,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        return MCPConnectionProbe(
            False,
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def call_connection_tool(
    definition: MCPConnectionDefinition,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call one Tool through a configured external MCP connection."""

    name = tool_name.strip()
    if not name:
        raise ValueError("MCP tool_name must not be empty")
    tool_arguments = dict(arguments)

    if definition.transport == "http":
        # Detect the protocol with a side-effect-free request before calling
        # the actual Tool. Falling back after a tools/call error could invoke
        # a destructive remote Tool twice.
        try:
            _result(
                _http_rpc(
                    definition,
                    _request("server/discover", _modern_params(), 1),
                    modern=True,
                    timeout=timeout,
                )
            )
        except Exception:
            initialize = _request(
                "initialize",
                {
                    "protocolVersion": LEGACY_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "micromatrix-workbench",
                        "version": "1",
                    },
                },
                1,
            )
            _result(_http_rpc(definition, initialize, modern=False, timeout=timeout))
            return _result(
                _http_rpc(
                    definition,
                    _request(
                        "tools/call",
                        {"name": name, "arguments": tool_arguments},
                        2,
                    ),
                    modern=False,
                    timeout=timeout,
                )
            )

        modern_params = {
            **_modern_params(),
            "name": name,
            "arguments": tool_arguments,
        }
        return _result(
            _http_rpc(
                definition,
                _request("tools/call", modern_params, 2),
                modern=True,
                timeout=timeout,
            )
        )

    initialize = _request(
        "initialize",
        {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "micromatrix-workbench", "version": "1"},
        },
        1,
    )
    call_request = _request(
        "tools/call",
        {"name": name, "arguments": tool_arguments},
        2,
    )
    responses = _stdio_roundtrip(
        definition,
        [initialize, call_request],
        timeout=timeout,
    )
    by_id = {item.get("id"): item for item in responses}
    _result(by_id[1])
    return _result(by_id[2])

