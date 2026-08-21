from __future__ import annotations

from dataclasses import replace

from ...core.tool import ToolAnnotations, ToolDefinition
from ...permissions.capabilities import Capability
from ...schemas import I, S, obj


WORKFLOW_OBJECT = {"type": "object", "additionalProperties": True}
SKILL_OBJECT = {"type": "object", "additionalProperties": True}
MCP_CONNECTION_OBJECT = {"type": "object", "additionalProperties": True}


WORKBENCH_TOOLS = (
    ToolDefinition(
        "workflow_authoring_context",
        "Workflow authoring context",
        "Return the Workflow schema vocabulary, Skill/Tool catalogs, existing workflows, condition language, and authoring safety rules for AI-generated workflows.",
        obj(),
        "workflow_authoring_context",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "skill_list",
        "List skills",
        "List AI Workbench Skills available in the current Runtime.",
        obj(),
        "skill_list",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "skill_get",
        "Get skill",
        "Return one AI Workbench Skill including its method document and artifacts.",
        obj({"skill_id": {**S, "minLength": 1}}, ("skill_id",)),
        "skill_get",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "skill_validate",
        "Validate skill",
        "Validate a Skill draft without persisting it.",
        obj({"skill": SKILL_OBJECT}, ("skill",)),
        "skill_validate",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "skill_save",
        "Save skill",
        "Validate and persist a global Skill using expected_version optimistic concurrency.",
        obj(
            {
                "skill": SKILL_OBJECT,
                "expected_version": {**I, "minimum": 0},
            },
            ("skill", "expected_version"),
        ),
        "skill_save",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "skill_delete",
        "Delete skill",
        "Delete a global Skill override/asset by id.",
        obj({"skill_id": {**S, "minLength": 1}}, ("skill_id",)),
        "skill_delete",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True, idempotent=True),
    ),
    ToolDefinition(
        "mcp_connection_list",
        "List MCP connections",
        "List global external MCP Connection assets and their discovery status.",
        obj(),
        "mcp_connection_list",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "mcp_connection_get",
        "Get MCP connection",
        "Return one global MCP Connection definition without resolving secret references.",
        obj({"connection_id": {**S, "minLength": 1}}, ("connection_id",)),
        "mcp_connection_get",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "mcp_connection_validate",
        "Validate MCP connection",
        "Validate an MCP Connection draft without persisting or connecting to it.",
        obj({"connection": MCP_CONNECTION_OBJECT}, ("connection",)),
        "mcp_connection_validate",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "mcp_connection_save",
        "Save MCP connection",
        "Persist a global MCP Connection using expected_version optimistic concurrency. Secrets must use *_refs fields.",
        obj(
            {
                "connection": MCP_CONNECTION_OBJECT,
                "expected_version": {**I, "minimum": 0},
            },
            ("connection", "expected_version"),
        ),
        "mcp_connection_save",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "mcp_connection_delete",
        "Delete MCP connection",
        "Delete a global MCP Connection by id; discovered tools disappear from the Effective Tool Catalog immediately.",
        obj({"connection_id": {**S, "minLength": 1}}, ("connection_id",)),
        "mcp_connection_delete",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True, idempotent=True),
    ),
    ToolDefinition(
        "mcp_connection_test",
        "Test MCP connection",
        "Connect to an enabled external MCP Server and verify protocol negotiation without changing its discovered Tool cache.",
        obj(
            {
                "connection_id": {**S, "minLength": 1},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            ("connection_id",),
        ),
        "mcp_connection_test",
        frozenset({Capability.PROCESS_EXECUTE}),
        ToolAnnotations(read_only=True),
    ),
    ToolDefinition(
        "mcp_connection_discover_tools",
        "Discover MCP tools",
        "Connect to an enabled external MCP Server, fetch tools/list, and update the global Effective Tool Catalog.",
        obj(
            {
                "connection_id": {**S, "minLength": 1},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            ("connection_id",),
        ),
        "mcp_connection_discover_tools",
        frozenset({Capability.PROCESS_EXECUTE, Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "mcp_connection_call_tool",
        "Call MCP connection tool",
        "Internal Workbench execution adapter used by Workflow Tool nodes to invoke one discovered external MCP Tool while preserving local permission checks.",
        obj(
            {
                "connection_id": {**S, "minLength": 1},
                "tool_name": {**S, "minLength": 1},
                "arguments": {"type": "object", "additionalProperties": True},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            ("connection_id", "tool_name", "arguments"),
        ),
        "mcp_connection_call_tool",
        frozenset({Capability.PROCESS_EXECUTE}),
        ToolAnnotations(open_world=True),
    ),
    ToolDefinition(
        "workflow_list",
        "List workflows",
        "Call at the beginning of each new user task to discover user-authored operating procedures, then start a clearly matching Workflow before ad-hoc task tool use. Returns description, inputs_schema, tags, version, scope, and graph size.",
        obj(),
        "workflow_list",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_get",
        "Get workflow",
        "Return one workspace Workflow definition by id.",
        obj({"workflow_id": {**S, "minLength": 1}}, ("workflow_id",)),
        "workflow_get",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_validate",
        "Validate workflow",
        "Validate a Workflow draft for authoring, including discovery metadata and capability references, without persisting it.",
        obj({"workflow": WORKFLOW_OBJECT}, ("workflow",)),
        "workflow_validate",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_save",
        "Save workflow",
        "Validate and persist a workspace Workflow definition using expected_version optimistic concurrency.",
        obj(
            {
                "workflow": WORKFLOW_OBJECT,
                "expected_version": {**I, "minimum": 0},
            },
            ("workflow", "expected_version"),
        ),
        "workflow_save",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "workflow_delete",
        "Delete workflow",
        "Delete a workspace Workflow definition by id.",
        obj({"workflow_id": {**S, "minLength": 1}}, ("workflow_id",)),
        "workflow_delete",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_export",
        "Export workflow",
        "Export a Workflow Definition as portable JSON without runtime-only scope/source metadata.",
        obj({"workflow_id": {**S, "minLength": 1}}, ("workflow_id",)),
        "workflow_export",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_import",
        "Import workflow",
        "Parse, validate, and save a portable Workflow JSON document using expected_version optimistic concurrency.",
        obj(
            {
                "document": {**S, "minLength": 2},
                "expected_version": {**I, "minimum": 0},
            },
            ("document", "expected_version"),
        ),
        "workflow_import",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "workflow_run_list",
        "List workflow runs",
        "List persisted Workflow Runs in the current Workspace.",
        obj(),
        "workflow_run_list",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_start",
        "Start workflow",
        "Start the user-authored Workflow that matches the current task before ad-hoc task tool use. Validates inputs against inputs_schema and advances until a model or approval boundary is reached; execute pending skill instructions and advance with workflow_continue.",
        obj(
            {
                "workflow_id": {**S, "minLength": 1},
                "inputs": {"type": "object", "additionalProperties": True},
            },
            ("workflow_id",),
        ),
        "workflow_start",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "workflow_status",
        "Workflow status",
        "Return the current persisted state of one Workflow Run.",
        obj({"run_id": {**S, "minLength": 1}}, ("run_id",)),
        "workflow_status",
        frozenset({Capability.FILESYSTEM_READ}),
        ToolAnnotations(read_only=True, idempotent=True),
    ),
    ToolDefinition(
        "workflow_continue",
        "Continue workflow",
        "Continue a Workflow Run. waiting_model accepts node_id/outcome/output; waiting_approval only consumes an already signed Desktop decision and accepts no decision fields.",
        obj(
            {
                "run_id": {**S, "minLength": 1},
                "node_id": {**S, "minLength": 1},
                "outcome": {**S, "enum": ["success", "failure"]},
                "output": {},
            },
            ("run_id",),
        ),
        "workflow_continue",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "workflow_retry",
        "Retry workflow",
        "Retry a failed Workflow Run from its last persisted checkpoint.",
        obj({"run_id": {**S, "minLength": 1}}, ("run_id",)),
        "workflow_retry",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(),
    ),
    ToolDefinition(
        "workflow_cancel",
        "Cancel workflow",
        "Cancel a Workflow Run. Completed runs remain in history.",
        obj({"run_id": {**S, "minLength": 1}}, ("run_id",)),
        "workflow_cancel",
        frozenset({Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True, idempotent=True),
    ),
)


_WORKBENCH_FINE_GRAINED_TOOLS = WORKBENCH_TOOLS
_WORKBENCH_MCP_FACADES = (
    ToolDefinition(
        "skill_manage",
        "Skill management",
        "Manage Workbench Skills through one domain tool. action=list|get|validate|save|delete.",
        obj(
            {
                "action": {**S, "enum": ["list", "get", "validate", "save", "delete"]},
                "skill_id": S,
                "skill": SKILL_OBJECT,
                "expected_version": {**I, "minimum": 0},
            },
            ("action",),
        ),
        "skill_manage",
        frozenset({Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True),
    ),
    ToolDefinition(
        "mcp_connection_manage",
        "MCP connection management",
        "Manage external MCP Connections. action=list|get|validate|save|delete|test|discover|call_tool.",
        obj(
            {
                "action": {**S, "enum": ["list", "get", "validate", "save", "delete", "test", "discover", "call_tool"]},
                "connection_id": S,
                "connection": MCP_CONNECTION_OBJECT,
                "expected_version": {**I, "minimum": 0},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                "tool_name": S,
                "arguments": {"type": "object", "additionalProperties": True},
            },
            ("action",),
        ),
        "mcp_connection_manage",
        frozenset({Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE, Capability.PROCESS_EXECUTE}),
        ToolAnnotations(destructive=True, open_world=True),
    ),
    ToolDefinition(
        "workflow_manage",
        "Workflow management",
        "Manage workspace Workflow definitions. action=list|get|validate|save|delete|export|import.",
        obj(
            {
                "action": {**S, "enum": ["list", "get", "validate", "save", "delete", "export", "import"]},
                "workflow_id": S,
                "workflow": WORKFLOW_OBJECT,
                "document": S,
                "expected_version": {**I, "minimum": 0},
            },
            ("action",),
        ),
        "workflow_manage",
        frozenset({Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True),
    ),
    ToolDefinition(
        "workflow_run",
        "Workflow run",
        "Operate Workflow Runs. action=list|start|status|continue|retry|cancel.",
        obj(
            {
                "action": {**S, "enum": ["list", "start", "status", "continue", "retry", "cancel"]},
                "workflow_id": S,
                "inputs": {"type": "object", "additionalProperties": True},
                "run_id": S,
                "node_id": S,
                "outcome": {**S, "enum": ["success", "failure"]},
                "output": {},
            },
            ("action",),
        ),
        "workflow_run",
        frozenset({Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE}),
        ToolAnnotations(destructive=True),
    ),
)

# Fine-grained Workbench commands remain registered for Desktop, Workflow Engine,
# tests, and internal Runtime calls. The MCP surface exposes only the authoring
# context plus domain facades so clients with small tool budgets still receive
# every Workbench capability.
WORKBENCH_TOOLS = tuple(
    tool
    if tool.name == "workflow_authoring_context"
    else replace(tool, mcp_exposed=False)
    for tool in _WORKBENCH_FINE_GRAINED_TOOLS
) + _WORKBENCH_MCP_FACADES
