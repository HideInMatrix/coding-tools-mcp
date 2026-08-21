from __future__ import annotations

import json
from typing import Any

from ...errors import ToolError
from ...protocol import current_request_context
from ...workbench.effective_tools import build_effective_tool_catalog
from ...workbench.mcp_connection_store import MCPConnectionVersionConflictError
from ...workbench.models import ResourceScope
from ...workbench.skill_store import SkillVersionConflictError
from ...workbench.store import WorkflowVersionConflictError
from ...workbench.tool_references import is_workbench_control_tool
from ...workbench.workflows import WorkflowDefinition, validate_workflow


def _contains_plain_secret(value: Any, *, parent_key: str = "") -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized.endswith("_ref"):
                continue
            if any(marker in normalized for marker in ("password", "secret", "token", "api_key", "apikey")):
                if item is not None and item != "" and item is not False:
                    return True
            if _contains_plain_secret(item, parent_key=normalized):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_plain_secret(item, parent_key=parent_key) for item in value)
    return False


class WorkbenchHandlers:
    def _refresh_capability_assets(self) -> None:
        assets = getattr(self, "capability_assets", None)
        if assets is not None:
            assets.refresh()

    def _refresh_workspace_workflows(self) -> None:
        """Reload workspace workflows without replacing the registry object.

        Desktop Workbench writes through WorkflowStore while a running MCP
        Runtime keeps the registry in memory. Refreshing the workspace layer in
        place keeps workflow_list/get/start synchronized with Desktop saves,
        updates, and deletes while preserving references held by RunManager.
        """
        self.workflow_registry.replace_scope(
            self.workflow_store.list(),
            scope=ResourceScope.WORKSPACE,
        )

    def workflow_authoring_context(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        self._refresh_workspace_workflows()
        system_tools = [
            definition
            for definition in self._tools
            if not is_workbench_control_tool(definition.name)
        ]
        connections = self.mcp_connections.list()
        effective_tools = build_effective_tool_catalog(system_tools, connections)
        return {
            "schema_version": 1,
            "workflow_contract": {
                "required_fields": [
                    "id",
                    "name",
                    "description",
                    "entry_node_id",
                    "nodes",
                    "edges",
                ],
                "discovery_fields": ["description", "inputs_schema", "tags"],
                "inputs_schema": {
                    "root_type": "object",
                    "purpose": "Defines and validates workflow_start.inputs",
                },
            },
            "node_types": {
                "skill": {"required_config": ["skill_id"], "optional_config": []},
                "tool": {
                    "required_config": ["provider", "tool_name"],
                    "optional_config": ["connection_id", "arguments"],
                },
                "approval": {"required_config": ["title"], "optional_config": ["description"]},
                "condition": {"required_config": ["expression"], "optional_config": []},
                "artifact": {
                    "required_config": ["artifact_id", "source_node_id"],
                    "optional_config": ["format"],
                },
            },
            "edge_conditions": [
                "success",
                "failure",
                "approved",
                "rejected",
                "true",
                "false",
            ],
            "condition_language": [
                "true",
                "false",
                "path.to.value",
                "!path.to.value",
                'path.to.value == "literal"',
                'path.to.value != "literal"',
            ],
            "skills": [item.summary() for item in self.skill_registry.list()],
            "tools": [item.to_dict() for item in effective_tools],
            "mcp_connections": [item.summary() for item in connections],
            "workflows": [item.summary() for item in self.workflow_registry.list()],
            "rules": [
                "Workflow must be a DAG and every node must be reachable from entry_node_id.",
                "Workflow Tool nodes cannot call Workbench control-plane tools.",
                "Use provider=system for MicroMatrix Workbench tools and provider=mcp + connection_id for discovered external MCP tools.",
                "Workspace Workflow description is required and should explain when the AI should use it.",
                "Define inputs_schema so workflow_list exposes a machine-readable workflow_start input contract.",
                "Plain passwords, tokens, secrets, and API keys are forbidden; use *_ref references.",
                "Run workflow_validate before workflow_save.",
                "workflow_save requires expected_version; use 0 for a new Workspace workflow or first Workspace override.",
                "If WORKFLOW_VERSION_CONFLICT is returned, reload the workflow and merge before saving again.",
            ],
            "ok": True,
        }

    def skill_list(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        skills = self.skill_registry.list()
        return {
            "skills": [item.summary() for item in skills],
            "count": len(skills),
            "ok": True,
        }

    def skill_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        skill_id = str(arguments.get("skill_id") or "").strip()
        skill = self.skill_registry.get(skill_id)
        if skill is None:
            raise ToolError("SKILL_NOT_FOUND", f"找不到 Skill: {skill_id}")
        return {"skill": skill.to_dict(), "ok": True}

    def _parse_skill(self, arguments: dict[str, Any]):
        raw = arguments.get("skill")
        if not isinstance(raw, dict):
            raise ToolError("SKILL_INVALID", "skill must be an object")
        if _contains_plain_secret(raw):
            raise ToolError(
                "SKILL_SECRET",
                "Skill 不能保存 Password/Token/Secret/API Key 明文；请使用引用或移除敏感值。",
            )
        try:
            return self.capability_assets.validate_skill(raw)
        except (TypeError, ValueError) as exc:
            raise ToolError("SKILL_INVALID", f"Skill 定义无效: {exc}") from exc

    def skill_validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        skill = self._parse_skill(arguments)
        return {"skill": skill.to_dict(), "ok": True}

    def skill_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        skill = self._parse_skill(arguments)
        expected_version = int(arguments.get("expected_version", 0))
        try:
            saved = self.capability_assets.save_skill(
                skill.to_dict(),
                expected_version=expected_version,
            )
        except SkillVersionConflictError as exc:
            raise ToolError(
                "SKILL_VERSION_CONFLICT",
                str(exc),
                category="conflict",
                details={
                    "skill_id": exc.skill_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
            ) from exc
        return {"saved": True, "skill": saved.to_dict(), "ok": True}

    def skill_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        skill_id = str(arguments.get("skill_id") or "").strip()
        try:
            deleted = self.capability_assets.delete_skill(skill_id)
        except ValueError as exc:
            raise ToolError("SKILL_INVALID_ID", str(exc)) from exc
        return {"skill_id": skill_id, "deleted": deleted, "ok": True}

    def skill_manage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        payload = {key: value for key, value in arguments.items() if key != "action"}
        handlers = {
            "list": self.skill_list,
            "get": self.skill_get,
            "validate": self.skill_validate,
            "save": self.skill_save,
            "delete": self.skill_delete,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ToolError("INVALID_ARGUMENT", f"unsupported skill_manage action: {action}", "validation")
        return handler(payload)

    def _mcp_connection_definition(self, connection_id: str):
        definition = self.mcp_connections.get(connection_id)
        if definition is None:
            raise ToolError(
                "MCP_CONNECTION_NOT_FOUND",
                f"找不到 MCP Connection: {connection_id}",
            )
        return definition

    def _require_mcp_connection_access(self, definition, *, operation: str) -> None:
        if definition.transport == "http":
            if not self._permission_granted("network"):
                raise ToolError(
                    "PERMISSION_REQUIRED",
                    f"{operation} 需要访问外部 MCP 网络端点。",
                    category="permission",
                    details={"permission": "network"},
                )
            return
        if not self._permission_granted("privileged_executable"):
            raise ToolError(
                "PERMISSION_REQUIRED",
                f"{operation} 需要启动用户配置的 stdio MCP 进程。",
                category="permission",
                details={"permission": "privileged_executable"},
            )

    def mcp_connection_list(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        definitions = self.mcp_connections.list()
        return {
            "connections": [item.summary() for item in definitions],
            "count": len(definitions),
            "ok": True,
        }

    def mcp_connection_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        connection_id = str(arguments.get("connection_id") or "").strip()
        definition = self._mcp_connection_definition(connection_id)
        return {"connection": definition.to_dict(), "ok": True}

    def mcp_connection_validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("connection")
        if not isinstance(raw, dict):
            raise ToolError("MCP_CONNECTION_INVALID", "connection must be an object")
        try:
            definition = self.mcp_connections.validate(raw)
        except (TypeError, ValueError) as exc:
            raise ToolError(
                "MCP_CONNECTION_INVALID",
                f"MCP Connection 定义无效: {exc}",
            ) from exc
        return {"connection": definition.to_dict(), "ok": True}

    def mcp_connection_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("connection")
        if not isinstance(raw, dict):
            raise ToolError("MCP_CONNECTION_INVALID", "connection must be an object")
        expected_version = int(arguments.get("expected_version", 0))
        try:
            saved = self.mcp_connections.save(raw, expected_version=expected_version)
        except MCPConnectionVersionConflictError as exc:
            raise ToolError(
                "MCP_CONNECTION_VERSION_CONFLICT",
                str(exc),
                category="conflict",
                details={
                    "connection_id": exc.connection_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ToolError("MCP_CONNECTION_INVALID", str(exc)) from exc
        return {"connection": saved.to_dict(), "saved": True, "ok": True}

    def mcp_connection_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        connection_id = str(arguments.get("connection_id") or "").strip()
        self._refresh_capability_assets()
        try:
            deleted = self.mcp_connections.delete(connection_id)
        except ValueError as exc:
            raise ToolError("MCP_CONNECTION_INVALID_ID", str(exc)) from exc
        return {"connection_id": connection_id, "deleted": deleted, "ok": True}

    def mcp_connection_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        connection_id = str(arguments.get("connection_id") or "").strip()
        definition = self._mcp_connection_definition(connection_id)
        self._require_mcp_connection_access(definition, operation="MCP Connection Test")
        probe = self.mcp_connections.test(
            connection_id,
            timeout=float(arguments.get("timeout_seconds", 8)),
        )
        if not probe.ok:
            raise ToolError(
                "MCP_CONNECTION_TEST_FAILED",
                probe.error or "MCP Connection Test failed",
                retryable=True,
            )
        return {
            "connection_id": connection_id,
            "protocol_version": probe.protocol_version,
            "elapsed_ms": probe.elapsed_ms,
            "ok": True,
        }

    def mcp_connection_discover_tools(self, arguments: dict[str, Any]) -> dict[str, Any]:
        connection_id = str(arguments.get("connection_id") or "").strip()
        definition = self._mcp_connection_definition(connection_id)
        self._require_mcp_connection_access(definition, operation="MCP Tool Discovery")
        persisted, probe = self.mcp_connections.discover(
            connection_id,
            timeout=float(arguments.get("timeout_seconds", 8)),
        )
        if not probe.ok:
            raise ToolError(
                "MCP_TOOL_DISCOVERY_FAILED",
                probe.error or "MCP Tool discovery failed",
                retryable=True,
                details={"connection": persisted.summary()},
            )
        effective = build_effective_tool_catalog(
            [
                item
                for item in self._tools
                if not is_workbench_control_tool(item.name)
            ],
            self.mcp_connections.list(),
        )
        return {
            "connection": persisted.to_dict(),
            "tools": [item.to_dict() for item in probe.tools],
            "effective_tools": [item.to_dict() for item in effective],
            "protocol_version": probe.protocol_version,
            "elapsed_ms": probe.elapsed_ms,
            "ok": True,
        }

    def mcp_connection_call_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        connection_id = str(arguments.get("connection_id") or "").strip()
        tool_name = str(arguments.get("tool_name") or "").strip()
        tool_arguments = arguments.get("arguments")
        if not isinstance(tool_arguments, dict):
            raise ToolError("MCP_TOOL_ARGUMENTS_INVALID", "arguments must be an object")
        definition = self._mcp_connection_definition(connection_id)
        self._require_mcp_connection_access(
            definition,
            operation=f"MCP Tool {tool_name}",
        )
        try:
            result = self.mcp_connections.call_tool(
                connection_id,
                tool_name,
                tool_arguments,
                timeout=float(arguments.get("timeout_seconds", 30)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError("MCP_TOOL_INVALID", str(exc)) from exc
        except Exception as exc:
            raise ToolError(
                "MCP_TOOL_CALL_FAILED",
                str(exc),
                retryable=True,
            ) from exc
        return {
            "connection_id": connection_id,
            "tool_name": tool_name,
            "result": result,
            "ok": not bool(result.get("isError")),
        }

    def mcp_connection_manage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        payload = {key: value for key, value in arguments.items() if key != "action"}
        handlers = {
            "list": self.mcp_connection_list,
            "get": self.mcp_connection_get,
            "validate": self.mcp_connection_validate,
            "save": self.mcp_connection_save,
            "delete": self.mcp_connection_delete,
            "test": self.mcp_connection_test,
            "discover": self.mcp_connection_discover_tools,
            "call_tool": self.mcp_connection_call_tool,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ToolError("INVALID_ARGUMENT", f"unsupported mcp_connection_manage action: {action}", "validation")
        return handler(payload)

    def _parse_workflow(self, arguments: dict[str, Any]) -> WorkflowDefinition:
        raw = arguments.get("workflow")
        if not isinstance(raw, dict):
            raise ToolError("WORKFLOW_INVALID", "workflow must be an object")
        if _contains_plain_secret(raw):
            raise ToolError(
                "WORKFLOW_SECRET",
                "Workflow 不能保存 Password/Token/Secret/API Key 明文；请使用 *_ref 引用。"
            )
        try:
            return WorkflowDefinition.from_mapping(raw)
        except (TypeError, ValueError) as exc:
            raise ToolError("WORKFLOW_INVALID", f"Workflow 定义无效: {exc}") from exc

    def _workflow_validation(self, workflow: WorkflowDefinition):
        effective = build_effective_tool_catalog(
            [
                definition
                for definition in self._tools
                if not is_workbench_control_tool(definition.name)
            ],
            self.mcp_connections.list(),
        )
        return validate_workflow(
            workflow,
            skill_ids={item.id for item in self.skill_registry.list()},
            tool_names={
                definition.name
                for definition in self._tools
                if not is_workbench_control_tool(definition.name)
            },
            tool_keys={item.key for item in effective},
        )

    def workflow_list(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_workspace_workflows()
        definitions = self.workflow_registry.list()
        return {
            "workflows": [item.summary() for item in definitions],
            "count": len(definitions),
            "ok": True,
        }

    def workflow_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_workspace_workflows()
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        definition = self.workflow_registry.get(workflow_id)
        if definition is None:
            raise ToolError("WORKFLOW_NOT_FOUND", f"找不到 Workflow: {workflow_id}")
        return {
            "workflow": {
                **definition.to_dict(),
                "scope": definition.scope.value,
                "source": definition.source,
            },
            "ok": True,
        }

    def workflow_validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        workflow = self._parse_workflow(arguments)
        result = self._workflow_validation(workflow)
        return {**result.to_dict(), "workflow": workflow.to_dict()}

    def workflow_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        workflow = self._parse_workflow(arguments)
        validation = self._workflow_validation(workflow)
        if not validation.ok:
            return {**validation.to_dict(), "saved": False, "workflow": workflow.to_dict()}
        expected_version = int(arguments.get("expected_version", 0))
        try:
            persisted = self.workflow_store.save(
                workflow,
                expected_version=expected_version,
            )
        except WorkflowVersionConflictError as exc:
            raise ToolError(
                "WORKFLOW_VERSION_CONFLICT",
                str(exc),
                category="conflict",
                details={
                    "workflow_id": exc.workflow_id,
                    "expected_version": exc.expected,
                    "actual_version": exc.actual,
                },
            ) from exc
        self.workflow_registry.register(persisted, replace=True)
        return {
            **validation.to_dict(),
            "saved": True,
            "workflow": persisted.to_dict(),
            "ok": True,
        }

    def workflow_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        try:
            deleted = self.workflow_store.delete(workflow_id)
        except ValueError as exc:
            raise ToolError("WORKFLOW_INVALID_ID", str(exc)) from exc
        if deleted:
            self.workflow_registry.remove(
                workflow_id,
                scope=ResourceScope.WORKSPACE,
            )
        return {
            "workflow_id": workflow_id,
            "deleted": deleted,
            "ok": True,
        }

    def workflow_export(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_workspace_workflows()
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        definition = self.workflow_registry.get(workflow_id)
        if definition is None:
            raise ToolError("WORKFLOW_NOT_FOUND", f"找不到 Workflow: {workflow_id}")
        return {
            "workflow_id": workflow_id,
            "document": json.dumps(
                definition.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "ok": True,
        }

    def workflow_import(self, arguments: dict[str, Any]) -> dict[str, Any]:
        document = str(arguments.get("document") or "")
        try:
            raw = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ToolError(
                "WORKFLOW_IMPORT_INVALID_JSON",
                f"Workflow import JSON 无效: {exc}",
            ) from exc
        if not isinstance(raw, dict):
            raise ToolError(
                "WORKFLOW_IMPORT_INVALID",
                "Workflow import document 必须是 JSON object",
            )
        return self.workflow_save(
            {
                "workflow": raw,
                "expected_version": int(arguments.get("expected_version", 0)),
            }
        )

    def workflow_manage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        payload = {key: value for key, value in arguments.items() if key != "action"}
        handlers = {
            "list": self.workflow_list,
            "get": self.workflow_get,
            "validate": self.workflow_validate,
            "save": self.workflow_save,
            "delete": self.workflow_delete,
            "export": self.workflow_export,
            "import": self.workflow_import,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ToolError("INVALID_ARGUMENT", f"unsupported workflow_manage action: {action}", "validation")
        return handler(payload)

    def workflow_run_list(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        runs = self.workflow_runs.list()
        return {
            "runs": [run.public_dict() for run in runs],
            "count": len(runs),
            "ok": True,
        }

    def workflow_start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self._refresh_capability_assets()
        self._refresh_workspace_workflows()
        workflow_id = str(arguments.get("workflow_id") or "").strip()
        raw_inputs = arguments.get("inputs")
        inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
        try:
            run = self.workflow_runs.start(
                workflow_id,
                inputs=inputs,
                context=current_request_context(),
            )
        except KeyError as exc:
            raise ToolError("WORKFLOW_NOT_FOUND", f"找不到 Workflow: {workflow_id}") from exc
        except ValueError as exc:
            raise ToolError("WORKFLOW_START_FAILED", str(exc)) from exc
        return {"run": run.public_dict(), "ok": True}

    def workflow_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        try:
            run = self.workflow_runs.get(run_id)
        except ValueError as exc:
            raise ToolError("WORKFLOW_RUN_INVALID_ID", str(exc)) from exc
        if run is None:
            raise ToolError("WORKFLOW_RUN_NOT_FOUND", f"找不到 Workflow Run: {run_id}")
        return {"run": run.public_dict(), "ok": True}

    def workflow_continue(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        node_id = str(arguments.get("node_id") or "").strip()
        outcome = str(arguments.get("outcome") or "").strip()
        output = arguments.get("output")
        try:
            current = self.workflow_runs.get(run_id)
            if current is None:
                raise KeyError(run_id)
            if current.status == "waiting_approval" and any(
                key in arguments for key in ("node_id", "outcome", "output")
            ):
                raise ValueError(
                    "waiting_approval does not accept decision fields; "
                    "respond in the Desktop UI first"
                )
            run = self.workflow_runs.continue_pending(
                run_id,
                node_id=node_id,
                outcome=outcome,
                output=output,
                context=current_request_context(),
            )
        except KeyError as exc:
            raise ToolError("WORKFLOW_RUN_NOT_FOUND", f"找不到 Workflow Run: {run_id}") from exc
        except ValueError as exc:
            raise ToolError("WORKFLOW_CONTINUE_FAILED", str(exc)) from exc
        return {"run": run.public_dict(), "ok": True}

    def workflow_retry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        try:
            run = self.workflow_runs.retry(
                run_id,
                context=current_request_context(),
            )
        except KeyError as exc:
            raise ToolError("WORKFLOW_RUN_NOT_FOUND", f"找不到 Workflow Run: {run_id}") from exc
        except ValueError as exc:
            raise ToolError("WORKFLOW_RETRY_FAILED", str(exc)) from exc
        return {"run": run.public_dict(), "ok": True}

    def workflow_cancel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        try:
            run = self.workflow_runs.cancel(run_id)
        except KeyError as exc:
            raise ToolError("WORKFLOW_RUN_NOT_FOUND", f"找不到 Workflow Run: {run_id}") from exc
        except ValueError as exc:
            raise ToolError("WORKFLOW_RUN_INVALID_ID", str(exc)) from exc
        return {"run": run.public_dict(), "ok": True}

    def workflow_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        payload = {key: value for key, value in arguments.items() if key != "action"}
        handlers = {
            "list": self.workflow_run_list,
            "start": self.workflow_start,
            "status": self.workflow_status,
            "continue": self.workflow_continue,
            "retry": self.workflow_retry,
            "cancel": self.workflow_cancel,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ToolError("INVALID_ARGUMENT", f"unsupported workflow_run action: {action}", "validation")
        return handler(payload)

