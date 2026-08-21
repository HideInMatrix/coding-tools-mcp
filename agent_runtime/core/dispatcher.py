"""Framework dispatcher that resolves tools without owning runtime policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..errors import RpcError
from ..schemas import validate_value
from .registry import ToolRegistry
from .tool import ToolDefinition


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        handler_source: object,
        *,
        enabled_features: frozenset[str] = frozenset(),
    ) -> None:
        self.registry = registry
        self.handler_source = handler_source
        self.definitions = registry.definitions(enabled_features=enabled_features)
        self._definitions = {definition.name: definition for definition in self.definitions}

    def is_mcp_exposed(self, name: str) -> bool:
        definition = self._definitions.get(name)
        return bool(definition is not None and definition.mcp_exposed)

    def resolve(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[ToolDefinition, ToolHandler]:
        definition = self._definitions.get(name)
        if definition is None:
            raise RpcError(-32602, f"Unknown tool: {name}", {"reason": "unknown_tool"})
        try:
            validate_value(arguments, definition.input_schema)
        except ValueError as exc:
            raise RpcError(-32602, str(exc), {"reason": "invalid_arguments"}) from exc
        handler = getattr(self.handler_source, definition.handler_name, None)
        if not callable(handler):
            raise RuntimeError(
                f"registered tool handler is missing: {definition.name} -> {definition.handler_name}"
            )
        return definition, handler
