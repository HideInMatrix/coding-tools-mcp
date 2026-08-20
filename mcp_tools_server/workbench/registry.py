from __future__ import annotations

from collections.abc import Iterable

from .models import ResourceScope
from .store import WorkflowStore
from .workflows import WorkflowDefinition

WORKFLOW_SCOPE_PRECEDENCE = (
    ResourceScope.WORKSPACE,
    ResourceScope.BUILTIN,
)


def _project_development_workflow() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "project-development",
        "name": "Project Development",
        "description": (
            "Spec-driven project iteration: requirements and design first, then "
            "implementation, tests, acceptance evidence, and final human confirmation."
        ),
        "version": 1,
        "entry_node_id": "work",
        "inputs_schema": {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "description": "本次项目迭代、功能或问题的目标描述",
                }
            },
            "additionalProperties": True,
        },
        "tags": ["project", "development", "spec"],
        "nodes": [
            {
                "id": "work",
                "type": "skill",
                "name": "Spec 驱动开发",
                "position": {"x": 80, "y": 120},
                "config": {
                    "skill_id": "spec-development",
                    "arguments": {"feature": "current project iteration"},
                },
            },
            {
                "id": "report",
                "type": "artifact",
                "name": "保存交付报告",
                "position": {"x": 350, "y": 120},
                "config": {
                    "artifact_id": "project-delivery-report",
                    "source_node_id": "work",
                    "format": "json",
                },
            },
            {
                "id": "approval",
                "type": "approval",
                "name": "人工确认",
                "position": {"x": 620, "y": 120},
                "config": {
                    "title": "确认实现与验收结果",
                    "description": (
                        "请检查需求/设计变更、代码实现、测试结果和交付报告后，"
                        "决定是否完成当前项目迭代。"
                    ),
                },
            },
        ],
        "edges": [
            {
                "id": "work-report",
                "source": "work",
                "target": "report",
                "condition": "success",
            },
            {
                "id": "report-approval",
                "source": "report",
                "target": "approval",
                "condition": "success",
            },
        ],
        "metadata": {
            "category": "default-project-workflow",
            "acceptance": [
                "需求、范围、非目标和验收条件已明确",
                "关键设计和领域模型变更先记录后实施",
                "实现按已确认计划推进并有可验证测试结果",
                "最终交付报告区分已验证、未验证和剩余风险",
            ],
            "example_run": {
                "inputs": {"feature": "next project iteration"},
                "expected_states": [
                    "waiting_model",
                    "waiting_approval",
                    "succeeded",
                ],
                "artifact_id": "project-delivery-report",
            },
        },
    }


BUILTIN_WORKFLOWS: tuple[dict[str, object], ...] = (
    _project_development_workflow(),
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
    for raw in BUILTIN_WORKFLOWS:
        registry.register(
            WorkflowDefinition.from_mapping(
                raw,
                scope=ResourceScope.BUILTIN,
                source="built-in",
            ),
            replace=True,
        )
    for definition in store.list():
        registry.register(definition, replace=True)
    return registry

