from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .mcp_connections import MCPConnectionDefinition


@dataclass(frozen=True, slots=True)
class EffectiveTool:
    provider: str
    tool_name: str
    description: str
    input_schema: dict[str, Any]
    connection_id: str = ""
    connection_name: str = ""

    @property
    def key(self) -> str:
        if self.provider == "mcp":
            return f"mcp:{self.connection_id}:{self.tool_name}"
        return f"system:{self.tool_name}"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "tool_name": self.tool_name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "key": self.key,
            "workflow_executable": True,
        }
        if self.provider == "mcp":
            payload["connection_id"] = self.connection_id
            payload["connection_name"] = self.connection_name
        return payload


def build_effective_tool_catalog(
    system_tools: Iterable[Any],
    connections: Iterable[MCPConnectionDefinition],
) -> tuple[EffectiveTool, ...]:
    values: list[EffectiveTool] = []
    for definition in system_tools:
        values.append(
            EffectiveTool(
                provider="system",
                tool_name=str(definition.name),
                description=str(definition.description),
                input_schema=dict(definition.input_schema),
            )
        )
    for connection in connections:
        if not connection.enabled:
            continue
        for tool in connection.tools:
            values.append(
                EffectiveTool(
                    provider="mcp",
                    connection_id=connection.id,
                    connection_name=connection.name,
                    tool_name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                )
            )
    return tuple(sorted(values, key=lambda item: item.key))

