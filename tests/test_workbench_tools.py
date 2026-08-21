from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_runtime.runtime import Runtime
from agent_runtime.server import MCPHTTPServer
from agent_runtime.workbench import (
    CapabilityAssetService,
    WorkflowDefinition,
    WorkflowStore,
)


def workflow() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "ai-generated",
        "name": "AI Generated",
        "description": "AI-authored workflow used for Workbench contract tests",
        "version": 1,
        "entry_node_id": "tool",
        "inputs_schema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "additionalProperties": True,
        },
        "tags": ["ai", "test"],
        "nodes": [
            {
                "id": "tool",
                "type": "tool",
                "name": "Info",
                "position": {"x": 0, "y": 0},
                "config": {
                    "provider": "system",
                    "tool_name": "server_info",
                    "arguments": {},
                },
            }
        ],
        "edges": [],
    }


def skill_asset() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "ai-skill",
        "name": "AI Skill",
        "description": "Skill created through MCP authoring tools",
        "artifacts": ["ai-report.md"],
        "method_document": "# AI Skill\n\nRead evidence first, then produce the report.",
    }


class WorkflowToolTests(unittest.TestCase):
    def test_ai_can_manage_and_discover_global_mcp_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            external_workspace = base / "external"
            local_workspace = base / "local"
            external_workspace.mkdir()
            local_workspace.mkdir()
            external_runtime = Runtime(
                external_workspace,
                global_asset_root=base / "external-assets",
            )
            server = MCPHTTPServer(("127.0.0.1", 0), external_runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            local_runtime = Runtime(
                local_workspace,
                permission_mode="trusted",
                global_asset_root=base / "local-assets",
            )
            try:
                host, port = server.server_address[:2]
                connection = {
                    "id": "test-server",
                    "name": "Test Server",
                    "transport": "http",
                    "endpoint": f"http://{host}:{port}/mcp",
                    "enabled": True,
                }
                saved = local_runtime.call_tool(
                    "mcp_connection_save",
                    {"connection": connection, "expected_version": 0},
                )["structuredContent"]
                self.assertTrue(saved["saved"])
                self.assertEqual(saved["connection"]["scope"], "global")

                discovered = local_runtime.call_tool(
                    "mcp_connection_discover_tools",
                    {"connection_id": "test-server", "timeout_seconds": 5},
                )["structuredContent"]
                self.assertTrue(discovered["ok"])
                self.assertIn("read_file", {item["name"] for item in discovered["tools"]})
                self.assertIn(
                    "mcp:test-server:read_file",
                    {item["key"] for item in discovered["effective_tools"]},
                )

                listed = local_runtime.call_tool(
                    "mcp_connection_list",
                    {},
                )["structuredContent"]
                summary = next(
                    item for item in listed["connections"] if item["id"] == "test-server"
                )
                self.assertGreater(summary["tool_count"], 0)

                authoring = local_runtime.workflow_authoring_context({})
                self.assertIn(
                    "test-server",
                    {item["id"] for item in authoring["mcp_connections"]},
                )
                self.assertIn(
                    "mcp:test-server:read_file",
                    {item["key"] for item in authoring["tools"]},
                )

                deleted = local_runtime.call_tool(
                    "mcp_connection_delete",
                    {"connection_id": "test-server"},
                )["structuredContent"]
                self.assertTrue(deleted["deleted"])
                after_delete = local_runtime.workflow_authoring_context({})
                self.assertNotIn(
                    "mcp:test-server:read_file",
                    {item["key"] for item in after_delete["tools"]},
                )
            finally:
                local_runtime.close()
                server.shutdown()
                server.server_close()
                external_runtime.close()
                thread.join(timeout=2)

    def test_global_skill_is_visible_across_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_root = root / "global-assets"
            workspace_a = root / "workspace-a"
            workspace_b = root / "workspace-b"
            workspace_a.mkdir()
            workspace_b.mkdir()
            runtime_a = Runtime(workspace_a, global_asset_root=global_root)
            runtime_b = Runtime(workspace_b, global_asset_root=global_root)
            try:
                saved_skill = runtime_a.skill_save(
                    {"skill": skill_asset(), "expected_version": 0}
                )

                skills_b = runtime_b.skill_list({})
                loaded_skill = runtime_b.skill_get({"skill_id": "ai-skill"})
            finally:
                runtime_a.close()
                runtime_b.close()

        self.assertTrue(saved_skill["saved"])
        self.assertIn("ai-skill", {item["id"] for item in skills_b["skills"]})
        self.assertEqual(loaded_skill["skill"]["scope"], "global")

    def test_ai_can_author_skill_with_version_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(
                workspace,
                global_asset_root=workspace / "global-assets",
            )
            try:
                tool_names = {item["name"] for item in runtime.list_tools()["tools"]}
                self.assertIn("skill_manage", tool_names)
                self.assertNotIn("skill_save", tool_names)
                self.assertNotIn("prompt_save", tool_names)

                skill_validation = runtime.call_tool(
                    "skill_manage",
                    {"action": "validate", "skill": skill_asset()},
                )["structuredContent"]
                saved_skill = runtime.call_tool(
                    "skill_manage",
                    {"action": "save", "skill": skill_asset(), "expected_version": 0},
                )["structuredContent"]
                skill_conflict = runtime.call_tool(
                    "skill_manage",
                    {"action": "save", "skill": skill_asset(), "expected_version": 0},
                )["structuredContent"]

                listed_skills = runtime.call_tool(
                    "skill_manage", {"action": "list"}
                )["structuredContent"]
                loaded_skill = runtime.call_tool(
                    "skill_manage",
                    {"action": "get", "skill_id": "ai-skill"},
                )["structuredContent"]

                deleted_skill = runtime.call_tool(
                    "skill_manage",
                    {"action": "delete", "skill_id": "ai-skill"},
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertTrue(skill_validation["ok"])
        self.assertTrue(saved_skill["saved"])
        self.assertEqual(saved_skill["skill"]["version"], 1)
        self.assertFalse(skill_conflict["ok"])
        self.assertEqual(skill_conflict["error"]["code"], "SKILL_VERSION_CONFLICT")
        self.assertIn("ai-skill", {item["id"] for item in listed_skills["skills"]})
        self.assertEqual(loaded_skill["skill"]["scope"], "global")
        self.assertTrue(deleted_skill["deleted"])

    def test_ai_can_author_skill_workflow_discover_and_start_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(
                workspace,
                global_asset_root=workspace / "global-assets",
            )
            authored_workflow = {
                "schema_version": 1,
                "id": "ai-end-to-end",
                "name": "AI End-to-End",
                "description": (
                    "Use this workflow when the user wants an AI-authored project analysis "
                    "with a reusable Skill."
                ),
                "version": 1,
                "entry_node_id": "analysis",
                "inputs_schema": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "The project topic or area to analyse",
                        }
                    },
                    "required": ["topic"],
                    "additionalProperties": False,
                },
                "tags": ["analysis", "ai-authored"],
                "nodes": [
                    {
                        "id": "analysis",
                        "type": "skill",
                        "name": "AI Skill",
                        "position": {"x": 80, "y": 80},
                        "config": {"skill_id": "ai-skill", "arguments": {}},
                    }
                ],
                "edges": [],
                "metadata": {"created_by": "ai"},
            }
            try:
                saved_skill = runtime.call_tool(
                    "skill_save",
                    {"skill": skill_asset(), "expected_version": 0},
                )["structuredContent"]
                validated = runtime.call_tool(
                    "workflow_validate",
                    {"workflow": authored_workflow},
                )["structuredContent"]
                saved = runtime.call_tool(
                    "workflow_save",
                    {"workflow": authored_workflow, "expected_version": 0},
                )["structuredContent"]

                listed = runtime.call_tool("workflow_list", {})["structuredContent"]
                summary = next(
                    item for item in listed["workflows"] if item["id"] == "ai-end-to-end"
                )

                missing_input = runtime.call_tool(
                    "workflow_start",
                    {"workflow_id": "ai-end-to-end", "inputs": {}},
                )["structuredContent"]

                stale_update = dict(authored_workflow)
                stale_update["description"] = "stale update"
                conflict = runtime.call_tool(
                    "workflow_save",
                    {"workflow": stale_update, "expected_version": 0},
                )["structuredContent"]

                current_update = dict(authored_workflow)
                current_update["description"] = (
                    "Updated discovery description for project analysis requests."
                )
                updated = runtime.call_tool(
                    "workflow_save",
                    {"workflow": current_update, "expected_version": 1},
                )["structuredContent"]

                started = runtime.call_tool(
                    "workflow_start",
                    {
                        "workflow_id": "ai-end-to-end",
                        "inputs": {"topic": "workflow architecture"},
                    },
                )["structuredContent"]
                loaded = runtime.call_tool(
                    "workflow_get",
                    {"workflow_id": "ai-end-to-end"},
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertTrue(saved_skill["saved"])
        self.assertTrue(validated["ok"])
        self.assertTrue(saved["saved"])
        self.assertEqual(summary["description"], authored_workflow["description"])
        self.assertEqual(summary["tags"], ["analysis", "ai-authored"])
        self.assertEqual(summary["inputs_schema"]["required"], ["topic"])
        self.assertFalse(missing_input["ok"])
        self.assertEqual(missing_input["error"]["code"], "WORKFLOW_START_FAILED")
        self.assertIn("inputs.topic is required", missing_input["error"]["message"])
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["code"], "WORKFLOW_VERSION_CONFLICT")
        self.assertEqual(updated["workflow"]["version"], 2)
        self.assertTrue(started["ok"])
        self.assertEqual(started["run"]["status"], "waiting_model")
        self.assertEqual(started["run"]["inputs"], {"topic": "workflow architecture"})
        self.assertEqual(loaded["workflow"]["version"], 2)
        self.assertEqual(loaded["workflow"]["metadata"]["created_by"], "ai")

    def test_running_runtime_refreshes_skill_assets_written_externally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            global_root = workspace / "global-assets"
            runtime = Runtime(workspace, global_asset_root=global_root)
            external = CapabilityAssetService(global_root=global_root)
            try:
                external.save_skill(skill_asset(), expected_version=0)
                runtime_skills = runtime.skill_list({})
                self.assertIn(
                    "ai-skill",
                    {item["id"] for item in runtime_skills["skills"]},
                )

                desktop_view = CapabilityAssetService(global_root=global_root)
                persisted = desktop_view.skill_registry.get("ai-skill")
                assert persisted is not None
                self.assertEqual(persisted.scope.value, "global")
            finally:
                runtime.close()

    def test_running_runtime_refreshes_workflows_written_by_desktop_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            runtime = Runtime(workspace)
            store = WorkflowStore(workspace)
            try:
                raw = workflow()
                raw["description"] = "Created by Desktop Workbench"
                saved = store.save(
                    WorkflowDefinition.from_mapping(raw),
                    expected_version=0,
                )

                listed = runtime.workflow_list({})
                first = next(
                    item for item in listed["workflows"] if item["id"] == "ai-generated"
                )
                self.assertEqual(first["description"], "Created by Desktop Workbench")
                self.assertEqual(first["version"], saved.version)

                updated_raw = workflow()
                updated_raw["description"] = "Updated by Desktop Workbench"
                updated = store.save(
                    WorkflowDefinition.from_mapping(updated_raw),
                    expected_version=saved.version,
                )
                loaded = runtime.workflow_get({"workflow_id": "ai-generated"})
                self.assertEqual(loaded["workflow"]["description"], "Updated by Desktop Workbench")
                self.assertEqual(loaded["workflow"]["version"], updated.version)

                started = runtime.workflow_start(
                    {"workflow_id": "ai-generated", "inputs": {}}
                )
                self.assertEqual(started["run"]["workflow_id"], "ai-generated")
                self.assertEqual(started["run"]["workflow_version"], updated.version)

                self.assertTrue(store.delete("ai-generated"))
                after_delete = runtime.workflow_list({})
                self.assertNotIn(
                    "ai-generated",
                    {item["id"] for item in after_delete["workflows"]},
                )
            finally:
                runtime.close()

    def test_ai_can_validate_save_list_and_get_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                validated = runtime.workflow_validate({"workflow": workflow()})
                saved = runtime.workflow_save(
                    {"workflow": workflow(), "expected_version": 0}
                )
                listed = runtime.workflow_list({})
                loaded = runtime.workflow_get({"workflow_id": "ai-generated"})
                second_save = runtime.workflow_save(
                    {"workflow": workflow(), "expected_version": 1}
                )
            finally:
                runtime.close()

        self.assertTrue(validated["ok"])
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["workflow"]["version"], 1)
        self.assertEqual(listed["count"], 1)
        self.assertIn("ai-generated", {item["id"] for item in listed["workflows"]})
        self.assertEqual(loaded["workflow"]["id"], "ai-generated")
        self.assertEqual(second_save["workflow"]["version"], 2)

    def test_default_workflow_is_not_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                loaded = runtime.call_tool(
                    "workflow_get",
                    {"workflow_id": "project-development"},
                )["structuredContent"]
            finally:
                runtime.close()

        self.assertFalse(loaded["ok"])

    def test_workflow_authoring_context_and_optimistic_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                context = runtime.workflow_authoring_context({})
                first = runtime.workflow_save(
                    {"workflow": workflow(), "expected_version": 0}
                )
                conflict = runtime.call_tool(
                    "workflow_save",
                    {"workflow": workflow(), "expected_version": 0},
                )["structuredContent"]
                second = runtime.workflow_save(
                    {"workflow": workflow(), "expected_version": 1}
                )
            finally:
                runtime.close()

        self.assertTrue(context["ok"])
        self.assertEqual(
            context["workflow_contract"]["discovery_fields"],
            ["description", "inputs_schema", "tags"],
        )
        self.assertIn("skill", context["node_types"])
        self.assertNotIn("prompt", context["node_types"])
        self.assertNotIn("prompts", context)
        self.assertIn("approved", context["edge_conditions"])
        self.assertTrue(first["saved"])
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["error"]["code"], "WORKFLOW_VERSION_CONFLICT")
        self.assertEqual(conflict["error"]["details"]["actual_version"], 1)
        self.assertEqual(second["workflow"]["version"], 2)

    def test_invalid_reference_is_not_saved(self) -> None:
        raw = workflow()
        raw["nodes"][0]["type"] = "skill"
        raw["nodes"][0]["config"] = {"skill_id": "missing-skill"}
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                result = runtime.workflow_save(
                    {"workflow": raw, "expected_version": 0}
                )
            finally:
                runtime.close()

        self.assertFalse(result["saved"])
        self.assertIn("unknown_skill", {item["code"] for item in result["errors"]})

    def test_plain_secret_is_rejected(self) -> None:
        raw = workflow()
        raw["nodes"][0]["config"] = {
            "provider": "system",
            "tool_name": "server_info",
            "api_key": "secret-value",
        }
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                with self.assertRaisesRegex(Exception, "不能保存"):
                    runtime.workflow_validate({"workflow": raw})
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
