from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..schemas import validate_value
from .mcp_connection_client import (
    MCPConnectionProbe,
    call_connection_tool,
    probe_connection,
)
from .mcp_connection_store import MCPConnectionStore
from .mcp_connections import MCPConnectionDefinition


class MCPConnectionService:
    def __init__(self, *, global_root: Path | None = None) -> None:
        self.store = MCPConnectionStore(global_root)

    def list(self) -> tuple[MCPConnectionDefinition, ...]:
        return self.store.list()

    def get(self, connection_id: str) -> MCPConnectionDefinition | None:
        return self.store.get(connection_id)

    def tool_keys(self) -> frozenset[str]:
        return frozenset(
            f"mcp:{connection.id}:{tool.name}"
            for connection in self.store.list()
            for tool in connection.tools
        )

    def validate(self, raw: Mapping[str, Any]) -> MCPConnectionDefinition:
        return MCPConnectionDefinition.from_mapping(raw)

    def save(
        self,
        raw: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> MCPConnectionDefinition:
        return self.store.save(
            self.validate(raw),
            expected_version=expected_version,
        )

    def delete(self, connection_id: str) -> bool:
        return self.store.delete(connection_id)

    def test(self, connection_id: str, *, timeout: float = 8.0) -> MCPConnectionProbe:
        definition = self.store.get(connection_id)
        if definition is None:
            raise KeyError(f"找不到 MCP Connection: {connection_id}")
        if not definition.enabled:
            raise ValueError("MCP Connection 已禁用")
        return probe_connection(definition, discover_tools=False, timeout=timeout)

    def discover(
        self,
        connection_id: str,
        *,
        timeout: float = 8.0,
    ) -> tuple[MCPConnectionDefinition, MCPConnectionProbe]:
        current = self.store.get(connection_id)
        if current is None:
            raise KeyError(f"找不到 MCP Connection: {connection_id}")
        if not current.enabled:
            raise ValueError("MCP Connection 已禁用")
        probe = probe_connection(current, discover_tools=True, timeout=timeout)
        updated = replace(
            current,
            tools=probe.tools if probe.ok else current.tools,
            last_discovered_at=int(time.time()),
            last_error="" if probe.ok else probe.error,
        )
        persisted = self.store.save(updated, expected_version=current.version)
        return persisted, probe

    def call_tool(
        self,
        connection_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        definition = self.store.get(connection_id)
        if definition is None:
            raise KeyError(f"找不到 MCP Connection: {connection_id}")
        if not definition.enabled:
            raise ValueError("MCP Connection 已禁用")
        discovered = next(
            (item for item in definition.tools if item.name == tool_name),
            None,
        )
        if discovered is None:
            raise ValueError(
                f"MCP Tool 未发现或已失效: {connection_id}:{tool_name}"
            )
        validate_value(dict(arguments), discovered.input_schema)
        return call_connection_tool(
            definition,
            tool_name,
            arguments,
            timeout=timeout,
        )

