from __future__ import annotations

from collections.abc import Iterable

from .models import ResourceScope
from .store import WorkflowStore
from .workflows import WorkflowDefinition

WORKFLOW_SCOPE_PRECEDENCE = (
    ResourceScope.WORKSPACE,
)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._layers: dict[ResourceScope, dict[str, WorkflowDefinition]] = {
            scope: {} for scope in ResourceScope
        }

    def register(self, definition: WorkflowDefinition, *, replace: bool = False) -> None:
        layer = self._layers[definition.scope]
        if not replace and definition.id in layer:
            raise ValueError(f"duplicate workflow registration: {definition.id}")
        layer[definition.id] = definition

    def remove(self, workflow_id: str, *, scope: ResourceScope) -> bool:
        return self._layers[scope].pop(workflow_id, None) is not None

    def replace_scope(
        self,
        definitions: Iterable[WorkflowDefinition],
        *,
        scope: ResourceScope,
    ) -> None:
        layer: dict[str, WorkflowDefinition] = {}
        for definition in definitions:
            if definition.scope is not scope:
                raise ValueError(
                    f"workflow scope mismatch: expected {scope.value}, got {definition.scope.value}"
                )
            layer[definition.id] = definition
        self._layers[scope] = layer

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        for scope in WORKFLOW_SCOPE_PRECEDENCE:
            definition = self._layers[scope].get(workflow_id)
            if definition is not None:
                return definition
        return None

    def list(self) -> tuple[WorkflowDefinition, ...]:
        ids = set().union(*(layer.keys() for layer in self._layers.values()))
        definitions = [self.get(workflow_id) for workflow_id in ids]
        return tuple(
            sorted(
                (item for item in definitions if item is not None),
                key=lambda item: item.id,
            )
        )


def build_workflow_registry(
    store: WorkflowStore,
) -> WorkflowRegistry:
    registry = WorkflowRegistry()
    for definition in store.list():
        registry.register(definition, replace=True)
    return registry

