from __future__ import annotations

import tempfile
import threading
import sys
import textwrap
import unittest
from pathlib import Path

from agent_runtime.runtime import Runtime
from agent_runtime.server import MCPHTTPServer
from agent_runtime.tools import build_tool_registry
from agent_runtime.workbench import (
    CapabilityAssetService,
    MCPConnectionService,
    MCPConnectionVersionConflictError,
    ResourceScope,
    WorkflowDefinition,
    build_effective_tool_catalog,
)


def http_connection(endpoint: str, *, connection_id: str = "external") -> dict[str, object]:
    return {
        "id": connection_id,
        "name": "External MCP",
        "transport": "http",
        "endpoint": endpoint,
        "arguments": [],
        "environment": {},
        "environment_refs": {},
        "headers": {},
        "header_refs": {},
        "enabled": True,
    }


class MCPConnectionDomainTests(unittest.TestCase):
    def test_global_connection_crud_version_and_plain_secret_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = MCPConnectionService(global_root=root)
            raw = http_connection("https://mcp.example.com/mcp")

            first = service.save(raw, expected_version=0)
            self.assertEqual(first.version, 1)
            self.assertEqual(first.scope, ResourceScope.GLOBAL)

            raw["name"] = "External MCP v2"
            second = service.save(raw, expected_version=1)
            self.assertEqual(second.version, 2)

            with self.assertRaises(MCPConnectionVersionConflictError):
                service.save(raw, expected_version=1)

            unsafe = http_connection("https://mcp.example.com/mcp", connection_id="unsafe")
            unsafe["headers"] = {"Authorization": "Bearer secret"}
            with self.assertRaisesRegex(ValueError, "secret-bearing"):
                service.validate(unsafe)

            referenced = http_connection("https://mcp.example.com/mcp", connection_id="referenced")
            referenced["header_refs"] = {"Authorization": "env:TEST_MCP_TOKEN"}
            definition = service.validate(referenced)
            self.assertEqual(definition.header_refs["Authorization"], "env:TEST_MCP_TOKEN")

            self.assertTrue(service.delete("external"))
            self.assertIsNone(service.get("external"))

    def test_http_connection_discovers_real_mcp_tools_and_builds_effective_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "external-workspace").mkdir()
            external_runtime = Runtime(
                base / "external-workspace",
                global_asset_root=base / "external-assets",
            )
            server = MCPHTTPServer(("127.0.0.1", 0), external_runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                endpoint = f"http://{host}:{port}/mcp"
                service = MCPConnectionService(global_root=base / "local-assets")
                saved = service.save(http_connection(endpoint), expected_version=0)
                self.assertEqual(saved.version, 1)

                tested = service.test("external", timeout=5)
                self.assertTrue(tested.ok, tested.error)
                self.assertTrue(tested.protocol_version)

                discovered, probe = service.discover("external", timeout=5)
                self.assertTrue(probe.ok, probe.error)
                self.assertGreater(len(discovered.tools), 0)
                read_file = next(item for item in discovered.tools if item.name == "read_file")
                self.assertTrue(read_file.description)
                self.assertEqual(read_file.input_schema.get("type"), "object")
                self.assertEqual(discovered.version, 2)
                self.assertGreater(discovered.last_discovered_at, 0)

                system_tools = [
                    item
                    for item in build_tool_registry().definitions(
                        enabled_features=frozenset({"view_image"})
                    )
                    if not item.name.startswith(("workflow_", "prompt_", "skill_", "mcp_connection_"))
                ]
                catalog = build_effective_tool_catalog(system_tools, service.list())
                self.assertIn("system:read_file", {item.key for item in catalog})
                self.assertIn("mcp:external:read_file", {item.key for item in catalog})

                remote_result = service.call_tool(
                    "external",
                    "server_info",
                    {},
                    timeout=5,
                )
                self.assertFalse(remote_result.get("isError", False))
                self.assertIsInstance(remote_result.get("structuredContent"), dict)

                local_workspace = base / "local-workspace"
                local_workspace.mkdir()
                local_runtime = Runtime(
                    local_workspace,
                    permission_mode="dangerous",
                    global_asset_root=base / "local-assets",
                )
                try:
                    workflow = WorkflowDefinition.from_mapping(
                        {
                            "schema_version": 1,
                            "id": "external-tool-workflow",
                            "name": "External Tool Workflow",
                            "description": "Execute a discovered MCP tool through the workflow runtime",
                            "version": 1,
                            "entry_node_id": "external-info",
                            "nodes": [
                                {
                                    "id": "external-info",
                                    "type": "tool",
                                    "name": "External server info",
                                    "config": {
                                        "provider": "mcp",
                                        "connection_id": "external",
                                        "tool_name": "server_info",
                                        "arguments": {},
                                    },
                                }
                            ],
                            "edges": [],
                        }
                    )
                    validation = local_runtime.workflow_engine.validate(workflow)
                    self.assertTrue(validation.ok, validation.to_dict())
                    state = local_runtime.workflow_engine.start(workflow)
                    executed = local_runtime.workflow_engine.execute_local(
                        workflow,
                        state,
                        "external-info",
                    )
                    self.assertEqual(executed.outcome, "success")
                    structured = executed.output.get("structuredContent", {})
                    self.assertTrue(structured.get("ok"))
                    self.assertEqual(structured.get("connection_id"), "external")
                    self.assertEqual(structured.get("tool_name"), "server_info")
                finally:
                    local_runtime.close()

                safe_workspace = base / "safe-workspace"
                safe_workspace.mkdir()
                safe_runtime = Runtime(
                    safe_workspace,
                    permission_mode="safe",
                    permission_broker_from_env=False,
                    global_asset_root=base / "local-assets",
                )
                try:
                    safe_state = safe_runtime.workflow_engine.start(workflow)
                    safe_result = safe_runtime.workflow_engine.execute_local(
                        workflow,
                        safe_state,
                        "external-info",
                    )
                    self.assertEqual(safe_result.outcome, "failure")
                    safe_structured = safe_result.output.get("structuredContent", {})
                    self.assertFalse(safe_structured.get("ok"))
                    self.assertEqual(
                        safe_structured.get("error", {}).get("code"),
                        "PERMISSION_REQUIRED",
                    )
                finally:
                    safe_runtime.close()

                assets = CapabilityAssetService(global_root=base / "local-assets")
                mcp_skill = {
                    "schema_version": 1,
                    "id": "external-reader",
                    "name": "External Reader",
                    "description": "Use the external MCP read_file capability",
                    "artifacts": [],
                    "method_document": "# External Reader\n\nUse the configured external MCP tool.",
                }
                saved_skill = assets.save_skill(mcp_skill, expected_version=0)
                self.assertNotIn("tool_references", saved_skill.to_dict())

                skill_workspace = base / "skill-workspace"
                skill_workspace.mkdir()
                skill_runtime = Runtime(
                    skill_workspace,
                    global_asset_root=base / "local-assets",
                )
                try:
                    skill_workflow = WorkflowDefinition.from_mapping(
                        {
                            "schema_version": 1,
                            "id": "external-skill-workflow",
                            "name": "External Skill Workflow",
                            "description": "Expose an MCP-backed Skill through a workflow model action",
                            "version": 1,
                            "entry_node_id": "external-skill",
                            "nodes": [
                                {
                                    "id": "external-skill",
                                    "type": "skill",
                                    "name": "External Skill",
                                    "config": {"skill_id": "external-reader"},
                                }
                            ],
                            "edges": [],
                        }
                    )
                    skill_state = skill_runtime.workflow_engine.start(skill_workflow)
                    action = skill_runtime.workflow_engine.model_action(
                        skill_workflow,
                        skill_state,
                        "external-skill",
                    )
                    self.assertNotIn("allowed_tool_references", action.to_dict())
                    self.assertNotIn("allowed_tools", action.to_dict())
                finally:
                    skill_runtime.close()

                disabled_raw = discovered.to_dict()
                disabled_raw["enabled"] = False
                disabled = service.save(disabled_raw, expected_version=discovered.version)
                catalog_after_disable = build_effective_tool_catalog(system_tools, service.list())
                self.assertNotIn("mcp:external:read_file", {item.key for item in catalog_after_disable})
                self.assertFalse(disabled.enabled)
                reloaded_assets = CapabilityAssetService(global_root=base / "local-assets")
                self.assertIsNotNone(reloaded_assets.skill_registry.get("external-reader"))

                disabled_workspace = base / "disabled-workspace"
                disabled_workspace.mkdir()
                disabled_runtime = Runtime(
                    disabled_workspace,
                    global_asset_root=base / "local-assets",
                )
                try:
                    disabled_validation = disabled_runtime.workflow_engine.validate(workflow)
                    self.assertFalse(disabled_validation.ok)
                    self.assertIn(
                        "unknown_tool",
                        {item.code for item in disabled_validation.errors},
                    )
                finally:
                    disabled_runtime.close()
            finally:
                server.shutdown()
                server.server_close()
                external_runtime.close()
                thread.join(timeout=2)

    def test_failed_rediscovery_preserves_last_good_tool_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "external-workspace"
            workspace.mkdir()
            external_runtime = Runtime(
                workspace,
                global_asset_root=base / "external-assets",
            )
            server = MCPHTTPServer(("127.0.0.1", 0), external_runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            service = MCPConnectionService(global_root=base / "local-assets")
            try:
                host, port = server.server_address[:2]
                service.save(
                    http_connection(f"http://{host}:{port}/mcp"),
                    expected_version=0,
                )
                healthy, probe = service.discover("external", timeout=5)
                self.assertTrue(probe.ok, probe.error)
                good_tool_names = [item.name for item in healthy.tools]
                self.assertTrue(good_tool_names)

                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

                degraded, failed = service.discover("external", timeout=0.5)
                self.assertFalse(failed.ok)
                self.assertEqual([item.name for item in degraded.tools], good_tool_names)
                self.assertTrue(degraded.last_error)
                self.assertGreater(degraded.version, healthy.version)
            finally:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
                external_runtime.close()
                thread.join(timeout=2)

    def test_stdio_connection_discovers_tools_with_bounded_process_client(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            server_script = base / "stdio_mcp.py"
            server_script.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys

                    for line in sys.stdin:
                        request = json.loads(line)
                        method = request.get("method")
                        request_id = request.get("id")
                        if method == "initialize":
                            result = {
                                "protocolVersion": "2025-11-25",
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": {"name": "fixture", "version": "1"},
                            }
                        elif method == "tools/list":
                            result = {
                                "tools": [
                                    {
                                        "name": "fixture_echo",
                                        "description": "Echo a fixture value",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {"value": {"type": "string"}},
                                        },
                                    }
                                ]
                            }
                        elif method == "tools/call":
                            params = request.get("params") or {}
                            result = {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": str((params.get("arguments") or {}).get("value", "")),
                                    }
                                ],
                                "structuredContent": {
                                    "ok": True,
                                    "value": (params.get("arguments") or {}).get("value", ""),
                                },
                                "isError": False,
                            }
                        else:
                            result = {}
                        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
                    """
                ),
                encoding="utf-8",
            )

            service = MCPConnectionService(global_root=base / "assets")
            saved = service.save(
                {
                    "schema_version": 1,
                    "id": "stdio-fixture",
                    "name": "stdio Fixture",
                    "transport": "stdio",
                    "command": sys.executable,
                    "arguments": [str(server_script)],
                    "enabled": True,
                },
                expected_version=0,
            )
            self.assertEqual(saved.version, 1)

            tested = service.test("stdio-fixture", timeout=3)
            self.assertTrue(tested.ok, tested.error)
            self.assertEqual(tested.protocol_version, "2025-11-25")

            discovered, probe = service.discover("stdio-fixture", timeout=3)
            self.assertTrue(probe.ok, probe.error)
            self.assertEqual([item.name for item in discovered.tools], ["fixture_echo"])
            self.assertEqual(discovered.tools[0].input_schema.get("type"), "object")

            called = service.call_tool(
                "stdio-fixture",
                "fixture_echo",
                {"value": "hello"},
                timeout=3,
            )
            self.assertFalse(called.get("isError", False))
            self.assertEqual(called["structuredContent"]["value"], "hello")


if __name__ == "__main__":
    unittest.main()

