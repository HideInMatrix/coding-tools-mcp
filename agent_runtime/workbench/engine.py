from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .effective_tools import build_effective_tool_catalog
from .tool_references import is_workbench_control_tool, tool_reference_from_node_config

from ..protocol import RequestContext
from .workflows import WorkflowDefinition, WorkflowNode, validate_workflow


_PATH_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_COMPARE_PATTERN = re.compile(
    r'^(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*'
    r'(?P<op>==|!=)\s*"(?P<literal>[^"\\]*)"$'
)


@dataclass(frozen=True, slots=True)
class EngineState:
    activated: tuple[str, ...]
    ready: tuple[str, ...]
    completed: tuple[str, ...] = ()
    outcomes: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return not self.ready and set(self.activated).issubset(self.completed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activated": list(self.activated),
            "ready": list(self.ready),
            "completed": list(self.completed),
            "outcomes": dict(self.outcomes),
            "outputs": dict(self.outputs),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EngineState":
        return cls(
            activated=tuple(str(item) for item in value.get("activated", [])),
            ready=tuple(str(item) for item in value.get("ready", [])),
            completed=tuple(str(item) for item in value.get("completed", [])),
            outcomes={
                str(key): str(item)
                for key, item in dict(value.get("outcomes", {})).items()
            },
            outputs=dict(value.get("outputs", {})),
        )


@dataclass(frozen=True, slots=True)
class ModelAction:
    node_id: str
    node_type: str
    messages: tuple[dict[str, Any], ...]
    skill: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "model_action",
            "node_id": self.node_id,
            "node_type": self.node_type,
            "messages": [dict(item) for item in self.messages],
        }
        if self.skill is not None:
            payload["skill"] = dict(self.skill)
        return payload


@dataclass(frozen=True, slots=True)
class LocalExecutionResult:
    state: EngineState
    node_id: str
    outcome: str
    output: Any


def _lookup_path(values: Mapping[str, Any], path: str) -> Any:
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def evaluate_condition(expression: str, values: Mapping[str, Any]) -> bool:
    """Evaluate the intentionally tiny Phase-4 condition language."""

    raw = expression.strip()
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    compare = _COMPARE_PATTERN.fullmatch(raw)
    if compare is not None:
        value = _lookup_path(values, compare.group("path"))
        expected = compare.group("literal")
        equal = str(value) == expected if value is not None else False
        return equal if compare.group("op") == "==" else not equal

    negate = raw.startswith("!")
    path = raw[1:] if negate else raw
    if not _PATH_PATTERN.fullmatch(path):
        raise ValueError(f"unsupported condition expression: {expression}")
    result = bool(_lookup_path(values, path))
    return not result if negate else result


class WorkflowEngine:
    """UI-independent Workflow scheduler and local node executor."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def _available_tool_names(self) -> frozenset[str]:
        return frozenset(
            definition.name
            for definition in self.runtime._tools
            if not is_workbench_control_tool(definition.name)
        )

    def _available_tool_keys(self) -> frozenset[str]:
        connections = getattr(self.runtime, "mcp_connections", None)
        if connections is None:
            return frozenset(f"system:{name}" for name in self._available_tool_names())
        effective = build_effective_tool_catalog(
            [
                definition
                for definition in self.runtime._tools
                if not is_workbench_control_tool(definition.name)
            ],
            connections.list(),
        )
        return frozenset(item.key for item in effective)

    def validate(self, workflow: WorkflowDefinition):
        return validate_workflow(
            workflow,
            skill_ids={item.id for item in self.runtime.skill_registry.list()},
            tool_names=set(self._available_tool_names()),
            tool_keys=set(self._available_tool_keys()),
        )

    def start(self, workflow: WorkflowDefinition) -> EngineState:
        validation = self.validate(workflow)
        if not validation.ok:
            messages = "; ".join(item.message for item in validation.errors)
            raise ValueError(f"workflow is not executable: {messages}")
        return EngineState(
            activated=(workflow.entry_node_id,),
            ready=(workflow.entry_node_id,),
        )

    @staticmethod
    def _node(workflow: WorkflowDefinition, node_id: str) -> WorkflowNode:
        for node in workflow.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    @staticmethod
    def _ordered_ids(workflow: WorkflowDefinition, values: set[str]) -> tuple[str, ...]:
        return tuple(node.id for node in workflow.nodes if node.id in values)

    def complete(
        self,
        workflow: WorkflowDefinition,
        state: EngineState,
        node_id: str,
        *,
        outcome: str,
        output: Any = None,
    ) -> EngineState:
        if node_id not in state.activated:
            raise ValueError(f"node is not activated: {node_id}")
        if node_id in state.completed:
            raise ValueError(f"node is already completed: {node_id}")

        completed = set(state.completed)
        completed.add(node_id)
        activated = set(state.activated)
        ready = set(state.ready)
        ready.discard(node_id)

        for edge in workflow.edges:
            if edge.source != node_id or edge.condition != outcome:
                continue
            if edge.target in activated or edge.target in completed:
                continue
            activated.add(edge.target)
            ready.add(edge.target)

        outcomes = dict(state.outcomes)
        outcomes[node_id] = outcome
        outputs = dict(state.outputs)
        outputs[node_id] = output
        return EngineState(
            activated=self._ordered_ids(workflow, activated),
            ready=self._ordered_ids(workflow, ready),
            completed=self._ordered_ids(workflow, completed),
            outcomes=outcomes,
            outputs=outputs,
        )

    def model_action(
        self,
        workflow: WorkflowDefinition,
        state: EngineState,
        node_id: str,
        *,
        inputs: Mapping[str, Any] | None = None,
    ) -> ModelAction:
        if node_id not in state.ready:
            raise ValueError(f"node is not ready: {node_id}")
        node = self._node(workflow, node_id)
        if node.type == "skill":
            skill_id = str(node.config.get("skill_id") or "")
            skill = self.runtime.skill_registry.get(skill_id)
            if skill is None:
                raise ValueError(f"unknown skill: {skill_id}")
            return ModelAction(
                node_id=node.id,
                node_type="skill",
                messages=(),
                skill={
                    "id": skill.id,
                    "name": skill.name,
                    "method_document": skill.method_document,
                    "artifacts": list(skill.artifacts),
                },
            )

        raise ValueError(f"node is not a model action: {node.type}")

    def execute_local(
        self,
        workflow: WorkflowDefinition,
        state: EngineState,
        node_id: str,
        *,
        context: RequestContext | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> LocalExecutionResult:
        if node_id not in state.ready:
            raise ValueError(f"node is not ready: {node_id}")
        node = self._node(workflow, node_id)

        if node.type == "tool":
            reference = tool_reference_from_node_config(node.config)
            raw_arguments = node.config.get("arguments", {})
            if not isinstance(raw_arguments, dict):
                raise ValueError("tool node arguments must be an object")
            if reference.provider == "system":
                result = self.runtime.call_tool(
                    reference.tool_name,
                    dict(raw_arguments),
                    context=context,
                )
            else:
                result = self.runtime.call_tool(
                    "mcp_connection_call_tool",
                    {
                        "connection_id": reference.connection_id,
                        "tool_name": reference.tool_name,
                        "arguments": dict(raw_arguments),
                    },
                    context=context,
                )
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            input_required = (
                isinstance(result, dict)
                and result.get("resultType") == "input_required"
            )
            failed = input_required or (
                isinstance(structured, dict) and structured.get("ok") is False
            )
            outcome = "failure" if failed else "success"
            next_state = self.complete(
                workflow,
                state,
                node_id,
                outcome=outcome,
                output=result,
            )
            return LocalExecutionResult(next_state, node_id, outcome, result)

        if node.type == "condition":
            expression = str(node.config.get("expression") or "")
            condition_values: dict[str, Any] = {
                "outputs": dict(state.outputs),
                "outcomes": dict(state.outcomes),
            }
            if values:
                condition_values.update(dict(values))
            decision = evaluate_condition(expression, condition_values)
            outcome = "true" if decision else "false"
            output = {"value": decision}
            next_state = self.complete(
                workflow,
                state,
                node_id,
                outcome=outcome,
                output=output,
            )
            return LocalExecutionResult(next_state, node_id, outcome, output)

        raise ValueError(f"node requires external handling: {node.type}")

