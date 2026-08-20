from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import PROMPT_ID_PATTERN, ResourceScope


SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:password|secret|token|api[_-]?key|authorization|cookie)",
    re.IGNORECASE,
)


def _string_mapping(value: Any, *, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        item = str(raw_value).strip()
        if not key or not item:
            raise ValueError(f"{field_name} keys and values must be non-empty strings")
        result[key] = item
    return result


def _reject_plain_secrets(value: Mapping[str, str], *, field_name: str) -> None:
    for key, item in value.items():
        if SENSITIVE_KEY_PATTERN.search(key) and item:
            raise ValueError(
                f"{field_name}.{key} looks secret-bearing; use the corresponding *_refs field"
            )


@dataclass(frozen=True, slots=True)
class DiscoveredMCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DiscoveredMCPTool":
        name = str(value.get("name") or "").strip()
        if not name:
            raise ValueError("discovered MCP tool name must not be empty")
        raw_schema = value.get("input_schema", value.get("inputSchema", {}))
        if not isinstance(raw_schema, dict):
            raise ValueError("discovered MCP tool input schema must be an object")
        return cls(
            name=name,
            description=str(value.get("description") or "").strip(),
            input_schema=dict(raw_schema),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


@dataclass(frozen=True, slots=True)
class MCPConnectionDefinition:
    id: str
    name: str
    transport: str
    endpoint: str = ""
    command: str = ""
    arguments: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    environment_refs: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    header_refs: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    version: int = 1
    schema_version: int = 1
    tools: tuple[DiscoveredMCPTool, ...] = ()
    last_discovered_at: int = 0
    last_error: str = ""
    scope: ResourceScope = ResourceScope.GLOBAL
    source: str = "global"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        scope: ResourceScope = ResourceScope.GLOBAL,
        source: str = "global",
    ) -> "MCPConnectionDefinition":
        try:
            schema_version = int(value.get("schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("MCP connection schema_version must be an integer") from exc
        if schema_version != 1:
            raise ValueError(f"unsupported MCP connection schema_version: {schema_version}")

        connection_id = str(value.get("id") or "").strip()
        if not PROMPT_ID_PATTERN.fullmatch(connection_id):
            raise ValueError(f"invalid MCP connection id: {connection_id!r}")
        name = str(value.get("name") or connection_id).strip()
        if not name:
            raise ValueError("MCP connection name must not be empty")
        transport = str(value.get("transport") or "").strip().lower()
        if transport not in {"stdio", "http"}:
            raise ValueError("MCP connection transport must be stdio or http")

        endpoint = str(value.get("endpoint") or "").strip()
        command = str(value.get("command") or "").strip()
        if transport == "http":
            if not endpoint.startswith(("http://", "https://")):
                raise ValueError("HTTP MCP connection requires an http(s) endpoint")
        elif not command:
            raise ValueError("stdio MCP connection requires command")

        raw_arguments = value.get("arguments", [])
        if not isinstance(raw_arguments, list) or any(not isinstance(item, str) for item in raw_arguments):
            raise ValueError("MCP connection arguments must be a list of strings")

        environment = _string_mapping(value.get("environment"), field_name="environment")
        headers = _string_mapping(value.get("headers"), field_name="headers")
        _reject_plain_secrets(environment, field_name="environment")
        _reject_plain_secrets(headers, field_name="headers")
        environment_refs = _string_mapping(
            value.get("environment_refs"),
            field_name="environment_refs",
        )
        header_refs = _string_mapping(value.get("header_refs"), field_name="header_refs")

        raw_tools = value.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError("MCP connection tools must be an array")
        if any(not isinstance(item, Mapping) for item in raw_tools):
            raise ValueError("MCP connection tools must contain objects")
        tools = tuple(DiscoveredMCPTool.from_mapping(item) for item in raw_tools)
        if len({item.name for item in tools}) != len(tools):
            raise ValueError("MCP connection discovered tool names must be unique")

        try:
            version = int(value.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("MCP connection version must be an integer") from exc
        if version < 1:
            raise ValueError("MCP connection version must be >= 1")

        return cls(
            id=connection_id,
            name=name,
            transport=transport,
            endpoint=endpoint,
            command=command,
            arguments=tuple(raw_arguments),
            environment=environment,
            environment_refs=environment_refs,
            headers=headers,
            header_refs=header_refs,
            enabled=bool(value.get("enabled", True)),
            version=version,
            schema_version=schema_version,
            tools=tools,
            last_discovered_at=max(0, int(value.get("last_discovered_at", 0) or 0)),
            last_error=str(value.get("last_error") or "").strip(),
            scope=scope,
            source=source,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "command": self.command,
            "enabled": self.enabled,
            "version": self.version,
            "tool_count": len(self.tools),
            "last_discovered_at": self.last_discovered_at,
            "last_error": self.last_error,
            "scope": self.scope.value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "schema_version": self.schema_version,
            "arguments": list(self.arguments),
            "environment": dict(self.environment),
            "environment_refs": dict(self.environment_refs),
            "headers": dict(self.headers),
            "header_refs": dict(self.header_refs),
            "tools": [item.to_dict() for item in self.tools],
            "source": self.source,
        }

