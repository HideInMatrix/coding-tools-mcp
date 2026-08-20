from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


TOOL_PROVIDER_VALUES = frozenset({"system", "mcp"})
CONNECTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
WORKBENCH_CONTROL_TOOL_PREFIXES = (
    "workflow_",
    "prompt_",
    "skill_",
    "mcp_connection_",
)


def is_workbench_control_tool(tool_name: str) -> bool:
    """Return whether a Tool belongs to the Workbench authoring/control plane."""

    return tool_name.startswith(WORKBENCH_CONTROL_TOOL_PREFIXES)


@dataclass(frozen=True, slots=True)
class ToolReference:
    """Stable reference to an executable capability.

    ``system`` references point at tools implemented by Coding Tools MCP.
    ``mcp`` references point at tools discovered from a user-managed external
    MCP connection.  Both providers are validated through the Effective Tool
    Catalog and are executable from Workflow Tool nodes.
    """

    provider: str
    tool_name: str
    connection_id: str | None = None

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ToolReference":
        provider = str(value.get("provider") or "").strip().lower()
        if provider not in TOOL_PROVIDER_VALUES:
            raise ValueError(f"unsupported tool provider: {provider!r}")

        tool_name = str(value.get("tool_name") or "").strip()
        if not tool_name:
            raise ValueError("tool reference requires tool_name")

        raw_connection = value.get("connection_id")
        connection_id = str(raw_connection).strip() if raw_connection is not None else None
        if provider == "system":
            if connection_id:
                raise ValueError("system tool reference must not define connection_id")
            connection_id = None
        else:
            if not connection_id or not CONNECTION_ID_PATTERN.fullmatch(connection_id):
                raise ValueError(f"invalid MCP connection_id: {connection_id!r}")

        return cls(
            provider=provider,
            tool_name=tool_name,
            connection_id=connection_id,
        )

    @property
    def key(self) -> str:
        if self.provider == "system":
            return f"system:{self.tool_name}"
        return f"mcp:{self.connection_id}:{self.tool_name}"

    def to_dict(self) -> dict[str, str]:
        payload = {
            "provider": self.provider,
            "tool_name": self.tool_name,
        }
        if self.connection_id is not None:
            payload["connection_id"] = self.connection_id
        return payload


def tool_reference_from_node_config(config: Mapping[str, Any]) -> ToolReference:
    return ToolReference.from_value(
        {
            "provider": config.get("provider"),
            "connection_id": config.get("connection_id"),
            "tool_name": config.get("tool_name"),
        }
    )

