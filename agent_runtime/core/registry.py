"""Tool registry used by the runtime dispatcher."""

from __future__ import annotations

from collections.abc import Iterable

from .tool import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"duplicate tool registration: {definition.name}")
        self._definitions[definition.name] = definition

    def register_many(self, definitions: Iterable[ToolDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self, *, enabled_features: frozenset[str] = frozenset()) -> list[ToolDefinition]:
        return [
            definition
            for definition in self._definitions.values()
            if definition.feature is None or definition.feature in enabled_features
        ]

    def __len__(self) -> int:
        return len(self._definitions)
