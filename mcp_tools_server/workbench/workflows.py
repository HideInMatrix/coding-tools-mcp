from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import ResourceScope
from .schema import validate_workbench_schema
from .tool_references import is_workbench_control_tool, tool_reference_from_node_config


WORKFLOW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WORKFLOW_NODE_TYPES = frozenset(
    {"prompt", "skill", "tool", "approval", "condition", "artifact"}
)
WORKFLOW_EDGE_CONDITIONS = frozenset(
    {"success", "failure", "approved", "rejected", "true", "false"}
)


@dataclass(frozen=True, slots=True)
class WorkflowPosition:
    x: float
    y: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WorkflowPosition":
        raw = value or {}
        x = float(raw.get("x", 0))
        y = float(raw.get("y", 0))
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("workflow node position must contain finite numbers")
        return cls(x=x, y=y)

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True, slots=True)
class WorkflowPolicy:
    approval: str = "none"
    on_error: str = "stop"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WorkflowPolicy":
        raw = value or {}
        approval = str(raw.get("approval") or "none")
        on_error = str(raw.get("on_error") or "stop")
        if approval not in {"none", "required"}:
            raise ValueError(f"unsupported workflow approval policy: {approval}")
        if on_error not in {"stop", "continue"}:
            raise ValueError(f"unsupported workflow on_error policy: {on_error}")
        return cls(approval=approval, on_error=on_error)

    def to_dict(self) -> dict[str, str]:
        return {"approval": self.approval, "on_error": self.on_error}


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    type: str
    name: str
    position: WorkflowPosition
    config: dict[str, Any] = field(default_factory=dict)
    policy: WorkflowPolicy = field(default_factory=WorkflowPolicy)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowNode":
        node_id = str(value.get("id") or "").strip()
        if not NODE_ID_PATTERN.fullmatch(node_id):
            raise ValueError(f"invalid workflow node id: {node_id!r}")
        node_type = str(value.get("type") or "").strip()
        if node_type not in WORKFLOW_NODE_TYPES:
            raise ValueError(f"unsupported workflow node type: {node_type}")
        name = str(value.get("name") or node_id).strip()
        if not name:
            raise ValueError("workflow node name must not be empty")
        raw_config = value.get("config", {})
        if not isinstance(raw_config, dict):
            raise ValueError("workflow node config must be an object")
        config = dict(raw_config)
        if node_type == "tool":
            reference = tool_reference_from_node_config(config)
            config.update(reference.to_dict())
        raw_position = value.get("position")
        if raw_position is not None and not isinstance(raw_position, Mapping):
            raise ValueError("workflow node position must be an object")
        raw_policy = value.get("policy")
        if raw_policy is not None and not isinstance(raw_policy, Mapping):
            raise ValueError("workflow node policy must be an object")
        return cls(
            id=node_id,
            type=node_type,
            name=name,
            position=WorkflowPosition.from_mapping(raw_position),
            config=config,
            policy=WorkflowPolicy.from_mapping(raw_policy),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "position": self.position.to_dict(),
            "config": dict(self.config),
            "policy": self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    id: str
    source: str
    target: str
    condition: str = "success"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowEdge":
        edge_id = str(value.get("id") or "").strip()
        if not NODE_ID_PATTERN.fullmatch(edge_id):
            raise ValueError(f"invalid workflow edge id: {edge_id!r}")
        source = str(value.get("source") or "").strip()
        target = str(value.get("target") or "").strip()
        condition = str(value.get("condition") or "success").strip()
        if not NODE_ID_PATTERN.fullmatch(source) or not NODE_ID_PATTERN.fullmatch(target):
            raise ValueError("workflow edge source/target must be valid node ids")
        if condition not in WORKFLOW_EDGE_CONDITIONS:
            raise ValueError(f"unsupported workflow edge condition: {condition}")
        return cls(id=edge_id, source=source, target=target, condition=condition)

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
        }


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    id: str
    name: str
    description: str
    version: int
    entry_node_id: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    inputs_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": True}
    )
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    scope: ResourceScope = ResourceScope.WORKSPACE
    source: str = "workspace"

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        scope: ResourceScope = ResourceScope.WORKSPACE,
        source: str = "workspace",
    ) -> "WorkflowDefinition":
        value = validate_workbench_schema(value, resource_type="workflow")
        schema_version = int(value.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(f"unsupported workflow schema_version: {schema_version}")
        workflow_id = str(value.get("id") or "").strip()
        if not WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
            raise ValueError(f"invalid workflow id: {workflow_id!r}")
        name = str(value.get("name") or workflow_id).strip()
        description = str(value.get("description") or "").strip()
        if not description:
            raise ValueError("workflow description must not be empty")
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("workflow version must be >= 1")
        entry_node_id = str(value.get("entry_node_id") or "").strip()
        raw_nodes = value.get("nodes", [])
        raw_edges = value.get("edges", [])
        raw_inputs_schema = value.get(
            "inputs_schema",
            {"type": "object", "additionalProperties": True},
        )
        raw_tags = value.get("tags", [])
        raw_metadata = value.get("metadata", {})
        if not isinstance(raw_nodes, list):
            raise ValueError("workflow nodes must be a list")
        if not isinstance(raw_edges, list):
            raise ValueError("workflow edges must be a list")
        if not isinstance(raw_inputs_schema, dict):
            raise ValueError("workflow inputs_schema must be an object")
        if raw_inputs_schema.get("type", "object") != "object":
            raise ValueError("workflow inputs_schema root type must be object")
        raw_properties = raw_inputs_schema.get("properties", {})
        raw_required = raw_inputs_schema.get("required", [])
        if not isinstance(raw_properties, dict):
            raise ValueError("workflow inputs_schema.properties must be an object")
        if not isinstance(raw_required, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_required
        ):
            raise ValueError("workflow inputs_schema.required must be a list of strings")
        if len(set(raw_required)) != len(raw_required):
            raise ValueError("workflow inputs_schema.required must be unique")
        if any(item not in raw_properties for item in raw_required):
            raise ValueError("workflow inputs_schema.required must reference declared properties")
        if not isinstance(raw_tags, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_tags
        ):
            raise ValueError("workflow tags must be a list of non-empty strings")
        tags = tuple(dict.fromkeys(item.strip() for item in raw_tags))
        if len(tags) != len(raw_tags):
            raise ValueError("workflow tags must be unique")
        if not isinstance(raw_metadata, dict):
            raise ValueError("workflow metadata must be an object")
        nodes = tuple(
            WorkflowNode.from_mapping(item)
            for item in raw_nodes
            if isinstance(item, Mapping)
        )
        edges = tuple(
            WorkflowEdge.from_mapping(item)
            for item in raw_edges
            if isinstance(item, Mapping)
        )
        if len(nodes) != len(raw_nodes) or len(edges) != len(raw_edges):
            raise ValueError("workflow nodes/edges must contain objects only")
        return cls(
            id=workflow_id,
            name=name,
            description=description,
            version=version,
            entry_node_id=entry_node_id,
            nodes=nodes,
            edges=edges,
            inputs_schema=dict(raw_inputs_schema),
            tags=tags,
            metadata=dict(raw_metadata),
            schema_version=schema_version,
            scope=scope,
            source=source,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "scope": self.scope.value,
            "inputs_schema": dict(self.inputs_schema),
            "tags": list(self.tags),
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "entry_node_id": self.entry_node_id,
            "inputs_schema": dict(self.inputs_schema),
            "tags": list(self.tags),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class WorkflowValidationIssue:
    code: str
    message: str
    subject: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.subject:
            payload["subject"] = self.subject
        return payload


@dataclass(frozen=True, slots=True)
class WorkflowValidationResult:
    errors: tuple[WorkflowValidationIssue, ...] = ()
    warnings: tuple[WorkflowValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def validate_workflow(
    workflow: WorkflowDefinition,
    *,
    prompt_ids: set[str] | None = None,
    skill_ids: set[str] | None = None,
    tool_names: set[str] | None = None,
    tool_keys: set[str] | None = None,
) -> WorkflowValidationResult:
    errors: list[WorkflowValidationIssue] = []
    warnings: list[WorkflowValidationIssue] = []
    node_ids = [node.id for node in workflow.nodes]
    edge_ids = [edge.id for edge in workflow.edges]
    node_id_set = set(node_ids)

    if not workflow.nodes:
        errors.append(WorkflowValidationIssue("empty_workflow", "Workflow 至少需要一个节点"))
    if len(node_ids) != len(node_id_set):
        errors.append(WorkflowValidationIssue("duplicate_node", "Workflow Node ID 必须唯一"))
    if len(edge_ids) != len(set(edge_ids)):
        errors.append(WorkflowValidationIssue("duplicate_edge", "Workflow Edge ID 必须唯一"))
    if workflow.entry_node_id not in node_id_set:
        errors.append(
            WorkflowValidationIssue(
                "invalid_entry",
                "entry_node_id 必须指向现有节点",
                workflow.entry_node_id,
            )
        )

    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_id_set}
    for edge in workflow.edges:
        if edge.source not in node_id_set:
            errors.append(
                WorkflowValidationIssue("missing_source", "Edge source 不存在", edge.id)
            )
            continue
        if edge.target not in node_id_set:
            errors.append(
                WorkflowValidationIssue("missing_target", "Edge target 不存在", edge.id)
            )
            continue
        if edge.source == edge.target:
            errors.append(
                WorkflowValidationIssue("self_loop", "第一版 Workflow 不允许自环", edge.id)
            )
            continue
        outgoing[edge.source].append(edge.target)

    if not errors:
        # Use Kahn's algorithm instead of recursive DFS so large generated
        # Workflows cannot hit Python's recursion limit during validation.
        indegree: dict[str, int] = {node_id: 0 for node_id in node_id_set}
        for source in node_id_set:
            for target in outgoing.get(source, []):
                indegree[target] += 1
        ready = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited_count = 0
        while ready:
            node_id = ready.pop()
            visited_count += 1
            for target in outgoing.get(node_id, []):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)

        if visited_count != len(node_id_set):
            errors.append(
                WorkflowValidationIssue(
                    "cycle_detected",
                    "第一版 Workflow 必须是 DAG，普通 Edge 不允许形成环",
                )
            )

    if workflow.entry_node_id in node_id_set:
        reachable: set[str] = set()
        stack = [workflow.entry_node_id]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(outgoing.get(node_id, []))
        for node_id in sorted(node_id_set - reachable):
            errors.append(
                WorkflowValidationIssue(
                    "unreachable_node",
                    "节点无法从 entry_node_id 到达",
                    node_id,
                )
            )

    for node in workflow.nodes:
        if node.type == "prompt":
            prompt_id = str(node.config.get("prompt_id") or "").strip()
            if not prompt_id:
                errors.append(
                    WorkflowValidationIssue("missing_prompt", "Prompt 节点必须配置 prompt_id", node.id)
                )
            elif prompt_ids is not None and prompt_id not in prompt_ids:
                errors.append(
                    WorkflowValidationIssue("unknown_prompt", "引用的 Prompt 不存在", node.id)
                )
        elif node.type == "skill":
            skill_id = str(node.config.get("skill_id") or "").strip()
            if not skill_id:
                errors.append(
                    WorkflowValidationIssue("missing_skill", "Skill 节点必须配置 skill_id", node.id)
                )
            elif skill_ids is not None and skill_id not in skill_ids:
                errors.append(
                    WorkflowValidationIssue("unknown_skill", "引用的 Skill 不存在", node.id)
                )
        elif node.type == "tool":
            try:
                reference = tool_reference_from_node_config(node.config)
            except ValueError as exc:
                errors.append(
                    WorkflowValidationIssue("invalid_tool_reference", str(exc), node.id)
                )
                continue
            tool_name = reference.tool_name
            if reference.provider == "system" and is_workbench_control_tool(tool_name):
                errors.append(
                    WorkflowValidationIssue(
                        "recursive_workflow_tool",
                        "Workflow Tool Node 不允许调用 Workbench 控制面 Tool",
                        node.id,
                    )
                )
            elif tool_keys is not None and reference.key not in tool_keys:
                errors.append(
                    WorkflowValidationIssue(
                        "unknown_tool",
                        "引用的 Tool 不存在、未发现或当前已禁用",
                        node.id,
                    )
                )
            elif reference.provider == "mcp" and tool_keys is None:
                errors.append(
                    WorkflowValidationIssue(
                        "mcp_tool_unavailable",
                        "MCP Tool 需要 Effective Tool Catalog 才能验证",
                        node.id,
                    )
                )
            elif (
                reference.provider == "system"
                and tool_keys is None
                and tool_names is not None
                and tool_name not in tool_names
            ):
                errors.append(
                    WorkflowValidationIssue("unknown_tool", "引用的 Tool 不存在", node.id)
                )
        elif node.type == "approval":
            title = str(node.config.get("title") or node.name).strip()
            if not title:
                errors.append(
                    WorkflowValidationIssue("invalid_approval", "Approval 节点必须有标题", node.id)
                )
        elif node.type == "condition":
            expression = str(node.config.get("expression") or "").strip()
            if not expression:
                errors.append(
                    WorkflowValidationIssue(
                        "invalid_condition",
                        "Condition 节点必须配置受限 expression",
                        node.id,
                    )
                )
        elif node.type == "artifact":
            artifact_id = str(node.config.get("artifact_id") or "").strip()
            source_node_id = str(node.config.get("source_node_id") or "").strip()
            artifact_format = str(node.config.get("format") or "json").strip()
            if not NODE_ID_PATTERN.fullmatch(artifact_id):
                errors.append(
                    WorkflowValidationIssue(
                        "invalid_artifact_id",
                        "Artifact 节点必须配置合法 artifact_id",
                        node.id,
                    )
                )
            if source_node_id not in node_id_set or source_node_id == node.id:
                errors.append(
                    WorkflowValidationIssue(
                        "invalid_artifact_source",
                        "Artifact source_node_id 必须指向另一个现有节点",
                        node.id,
                    )
                )
            if artifact_format not in {"json", "text"}:
                errors.append(
                    WorkflowValidationIssue(
                        "invalid_artifact_format",
                        "Artifact format 只支持 json/text",
                        node.id,
                    )
                )

    artifact_ids = [
        str(node.config.get("artifact_id") or "").strip()
        for node in workflow.nodes
        if node.type == "artifact"
    ]
    valid_artifact_ids = [item for item in artifact_ids if item]
    if len(valid_artifact_ids) != len(set(valid_artifact_ids)):
        errors.append(
            WorkflowValidationIssue(
                "duplicate_artifact_id",
                "Workflow 中 artifact_id 必须唯一",
            )
        )

    return WorkflowValidationResult(tuple(errors), tuple(warnings))

