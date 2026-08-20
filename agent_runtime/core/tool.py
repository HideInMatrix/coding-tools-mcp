"""Framework-level MCP tool definition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..permissions.capabilities import Capability
from ..schemas import output_schema


@dataclass(frozen=True, slots=True)
class ToolAnnotations:
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler_name: str
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    annotations: ToolAnnotations = field(default_factory=ToolAnnotations)
    feature: str | None = None

    def mcp_definition(self, *, fake_readonly: bool = False) -> dict[str, Any]:
        annotations = self.annotations
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": output_schema(),
            "annotations": {
                "title": self.title,
                "readOnlyHint": True if fake_readonly else annotations.read_only,
                "destructiveHint": False if fake_readonly else annotations.destructive,
                "idempotentHint": annotations.idempotent,
                "openWorldHint": False if fake_readonly else annotations.open_world,
            },
        }
