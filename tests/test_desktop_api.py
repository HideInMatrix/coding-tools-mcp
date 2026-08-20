from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench.desktop_api import DesktopAPI
from agent_workbench.gateway_launcher import (
    GatewayDiagnosticReport,
    GatewayProfileDiagnostic,
)
from agent_runtime.local_permission_broker import LocalWorkflowApprovalBrokerClient
from agent_runtime.runtime import Runtime


class DesktopAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.settings: dict[str, object] = {}
        self.patches = [
            patch(
                "agent_workbench.server_profiles.settings_dir",
                return_value=self.base,
            ),
            patch(
                "agent_workbench.gateway_profiles.settings_dir",
                return_value=self.base,
            ),
            patch(
                "agent_workbench.oauth_persistence.settings_dir",
                return_value=self.base,
            ),
            patch(
                "agent_workbench.desktop_api.load_settings",
                side_effect=lambda: dict(self.settings),
            ),
            patch(
                "agent_workbench.desktop_api.save_settings",
                side_effect=lambda value: self.settings.update(value),
            ),
            patch(
                "agent_workbench.desktop_api.settings_dir",
                return_value=self.base,
            ),
        ]
        for item in self.patches:
            item.start()
        self.api = DesktopAPI()

    def tearDown(self) -> None:
        self.api._close()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def payload(self, *, name: str = "Server A", port: int = 8234) -> dict[str, object]:
        return {
            "name": name,
            "workspace": str(self.base),
            "oauth_password": "password",
            "host": "127.0.0.1",
            "port": port,
            "remember_secrets": True,
            "permission_mode": "safe",
            "network": {
                "provider": "external",
                "public_url": f"https://{name.lower().replace(' ', '-')}.example.com",
                "options": {},
            },
        }

    def gateway_payload(
        self,
        *,
        name: str = "Gateway A",
        port: int = 8234,
        hostname: str = "https://mcp.example.com",
    ) -> dict[str, object]:
        return {
            "name": name,
            "host": "127.0.0.1",
            "port": port,
            "remember_secrets": True,
            "network": {
                "provider": "cloudflare",
                "public_url": hostname,
                "options": {"tunnel_token": "gateway-token"},
            },
            "members": [
                {
                    "name": "Company",
                    "workspace": str(self.base),
                    "oauth_password": "company-password",
                    "instance_path": "/company",
                    "permission_mode": "safe",
                },
                {
                    "name": "Home",
                    "workspace": str(self.base),
                    "oauth_password": "home-password",
                    "instance_path": "/home",
                    "permission_mode": "trusted",
                },
            ],
        }

    def test_create_server_returns_serializable_profile(self) -> None:
        created = self.api.create_server(self.payload())
        self.assertEqual(created["name"], "Server A")
        self.assertEqual(created["port"], 8234)
        self.assertEqual(created["lifecycle"], "persistent")
        self.assertEqual(created["permission_mode"], "safe")
        self.assertFalse(created["running"])
        self.assertTrue(created["server_id"])

    def test_clear_logs_removes_retained_entries_without_resetting_cursor(self) -> None:
        self.api._append_log("first")
        self.api._append_log("second")
        before = self.api.get_logs()
        self.assertEqual([item["message"] for item in before["entries"]], ["first", "second"])

        cursor = self.api.clear_logs()
        self.assertEqual(cursor, before["cursor"])
        self.assertEqual(self.api.get_logs()["entries"], [])

        self.api._append_log("third")
        after = self.api.get_logs(cursor)
        self.assertEqual([item["message"] for item in after["entries"]], ["third"])

    def test_workflow_approval_bridge_lists_and_responds_signed_request(self) -> None:
        created = self.api.create_server(self.payload())
        server_id = str(created["server_id"])
        client = LocalWorkflowApprovalBrokerClient.from_values(
            directory=self.api.permission_broker.directory,
            secret_hex=self.api.permission_broker.secret.hex(),
            server_id=server_id,
        )
        request_id = client.publish(
            run_id="c" * 24,
            node_id="approval",
            approval_id="approval_3",
            title="确认实现",
            description="确认后继续",
        )

        pending = self.api.list_workflow_approvals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["request_id"], request_id)
        self.assertEqual(pending[0]["server_name"], "Server A")
        self.assertTrue(self.api.respond_workflow_approval(request_id, True))
        self.assertTrue(
            client.consume_response(
                request_id,
                run_id="c" * 24,
                node_id="approval",
                approval_id="approval_3",
            )
        )

    def test_workbench_target_catalog_and_workflow_crud(self) -> None:
        created = self.api.create_server(self.payload())
        server_id = str(created["server_id"])
        target_id = f"server:{server_id}"

        targets = self.api.list_workbench_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["target_id"], target_id)
        self.assertEqual(targets[0]["workspace"], str(self.base.resolve()))

        catalog = self.api.get_workbench_catalog(target_id)
        self.assertEqual([], catalog["workflows"])
        self.assertEqual([], catalog["skills"])

        raw = {
            "schema_version": 1,
            "id": "desktop-flow",
            "name": "Desktop Flow",
            "description": "Desktop-managed code review workflow",
            "version": 1,
            "entry_node_id": "review",
            "inputs_schema": {
                "type": "object",
                "properties": {"scope": {"type": "string"}},
                "additionalProperties": False,
            },
            "tags": ["review", "desktop"],
            "nodes": [
                {
                    "id": "review",
                    "type": "tool",
                    "name": "Review",
                    "position": {"x": 80, "y": 80},
                    "config": {
                        "provider": "system",
                        "tool_name": "server_info",
                        "arguments": {},
                    },
                }
            ],
            "edges": [],
        }
        validation = self.api.validate_workbench_workflow(target_id, raw)
        self.assertTrue(validation["ok"])
        saved = self.api.save_workbench_workflow(target_id, raw, 0)
        self.assertTrue(saved["saved"])
        loaded = self.api.get_workbench_workflow(target_id, "desktop-flow")
        self.assertEqual(loaded["name"], "Desktop Flow")
        self.assertEqual(loaded["scope"], "workspace")
        self.assertEqual(loaded["tags"], ["review", "desktop"])
        self.assertEqual(loaded["inputs_schema"]["properties"]["scope"]["type"], "string")

        with self.assertRaisesRegex(RuntimeError, "version conflict"):
            self.api.save_workbench_workflow(target_id, raw, 0)

    def test_workbench_catalog_sees_workflow_authored_by_running_ai_runtime(self) -> None:
        created = self.api.create_server(self.payload())
        target_id = f"server:{created['server_id']}"
        runtime = Runtime(
            self.base,
            global_asset_root=self.base / "workbench",
        )
        authored = {
            "schema_version": 1,
            "id": "ai-visible-in-gui",
            "name": "AI Visible In GUI",
            "description": "Analyse a requested project area and return reusable evidence.",
            "version": 1,
            "entry_node_id": "analysis",
            "inputs_schema": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
                "additionalProperties": False,
            },
            "tags": ["analysis", "ai-authored"],
            "nodes": [
                {
                    "id": "analysis",
                    "type": "tool",
                    "name": "Project Analysis",
                    "position": {"x": 80, "y": 80},
                    "config": {
                        "provider": "system",
                        "tool_name": "server_info",
                        "arguments": {},
                    },
                }
            ],
            "edges": [],
            "metadata": {"created_by": "ai"},
        }
        try:
            saved = runtime.workflow_save(
                {"workflow": authored, "expected_version": 0}
            )
        finally:
            runtime.close()

        self.assertTrue(saved["saved"])
        catalog = self.api.get_workbench_catalog(target_id)
        summary = next(
            item for item in catalog["workflows"] if item["id"] == "ai-visible-in-gui"
        )
        self.assertEqual(summary["description"], authored["description"])
        self.assertEqual(summary["tags"], ["analysis", "ai-authored"])
        self.assertEqual(summary["inputs_schema"]["required"], ["topic"])

        loaded = self.api.get_workbench_workflow(target_id, "ai-visible-in-gui")
        self.assertEqual(loaded["nodes"][0]["config"]["tool_name"], "server_info")
        self.assertEqual(loaded["metadata"]["created_by"], "ai")

    def test_workbench_skill_domain_crud(self) -> None:
        skill = {
            "schema_version": 1,
            "id": "desktop-skill",
            "name": "Desktop Skill",
            "description": "Managed Skill",
            "artifacts": ["desktop-report.md"],
            "method_document": "# Desktop Skill\n\nRead evidence and write the report.",
        }
        validated_skill = self.api.validate_workbench_skill(skill)
        self.assertTrue(validated_skill["ok"])
        saved_skill = self.api.save_workbench_skill(skill, 0)
        self.assertTrue(saved_skill["saved"])
        self.assertEqual(saved_skill["skill"]["version"], 1)
        self.assertEqual(saved_skill["skill"]["scope"], "global")
        self.assertNotIn("entry_prompt", saved_skill["skill"])
        self.assertNotIn("tool_references", saved_skill["skill"])

        catalog = self.api.get_workbench_capability_catalog()
        skill_summary = next(
            item for item in catalog["skills"] if item["id"] == "desktop-skill"
        )
        self.assertEqual(skill_summary["version"], 1)
        self.assertNotIn("prompts", catalog)

        created = self.api.create_server(self.payload())
        target_id = f"server:{created['server_id']}"
        workflow_catalog = self.api.get_workbench_catalog(target_id)
        self.assertIn(
            "desktop-skill",
            {item["id"] for item in workflow_catalog["skills"]},
        )
        self.assertNotIn("prompts", workflow_catalog)

        self.assertTrue(self.api.delete_workbench_skill("desktop-skill"))

        refreshed = self.api.get_workbench_capability_catalog()
        self.assertNotIn("desktop-skill", {item["id"] for item in refreshed["skills"]})

    def test_workbench_mcp_connection_global_crud_and_catalog(self) -> None:
        connection = {
            "id": "desktop-mcp",
            "name": "Desktop MCP",
            "transport": "http",
            "endpoint": "https://mcp.example.com/mcp",
            "enabled": True,
            "headers": {"Accept-Language": "zh-CN"},
            "header_refs": {"Authorization": "env:DESKTOP_MCP_AUTH"},
        }

        validated = self.api.validate_workbench_mcp_connection(connection)
        self.assertTrue(validated["ok"])

        saved = self.api.save_workbench_mcp_connection(connection, 0)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["connection"]["version"], 1)
        self.assertEqual(saved["connection"]["scope"], "global")

        loaded = self.api.get_workbench_mcp_connection("desktop-mcp")
        self.assertEqual(loaded["name"], "Desktop MCP")
        self.assertEqual(loaded["header_refs"]["Authorization"], "env:DESKTOP_MCP_AUTH")

        catalog = self.api.get_workbench_capability_catalog()
        self.assertIn(
            "desktop-mcp",
            {item["id"] for item in catalog["mcp_connections"]},
        )

        connection["enabled"] = False
        updated = self.api.save_workbench_mcp_connection(connection, 1)
        self.assertEqual(updated["connection"]["version"], 2)
        self.assertFalse(updated["connection"]["enabled"])

        unsafe = dict(connection)
        unsafe["id"] = "unsafe-mcp"
        unsafe["headers"] = {"Authorization": "Bearer plaintext"}
        with self.assertRaisesRegex(ValueError, "secret-bearing"):
            self.api.validate_workbench_mcp_connection(unsafe)

        self.assertTrue(self.api.delete_workbench_mcp_connection("desktop-mcp"))
        refreshed = self.api.get_workbench_capability_catalog()
        self.assertNotIn(
            "desktop-mcp",
            {item["id"] for item in refreshed["mcp_connections"]},
        )

    def test_workbench_gateway_profiles_are_separate_targets(self) -> None:
        gateway = self.api.create_gateway(self.gateway_payload())
        targets = self.api.list_workbench_targets()
        gateway_targets = [
            item
            for item in targets
            if str(item["target_id"]).startswith(f"gateway:{gateway['gateway_id']}:")
        ]
        self.assertEqual(len(gateway_targets), 2)
        self.assertEqual(
            {item["profile_name"] for item in gateway_targets},
            {"Company", "Home"},
        )

    def test_next_port_advances_after_profile_creation(self) -> None:
        self.api.create_server(self.payload())
        self.assertEqual(self.api.get_next_port(), 8235)

    def test_permission_mode_can_be_saved_and_updated(self) -> None:
        payload = self.payload()
        payload["permission_mode"] = "trusted"
        created = self.api.create_server(payload)
        self.assertEqual(created["permission_mode"], "trusted")

        payload["permission_mode"] = "dangerous"
        updated = self.api.update_server(str(created["server_id"]), payload)
        self.assertEqual(updated["permission_mode"], "dangerous")

    def test_duplicate_port_is_rejected(self) -> None:
        self.api.create_server(self.payload())
        with self.assertRaisesRegex(ValueError, "相同地址"):
            self.api.create_server(self.payload(name="Server B", port=8234))

    def test_same_public_hostname_is_rejected_even_with_different_paths(self) -> None:
        first = self.payload(name="Server A", port=8234)
        first["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com/company",
            "options": {"tunnel_token": "token-a"},
        }
        second = self.payload(name="Server B", port=8235)
        second["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com/home",
            "options": {"tunnel_token": "token-b"},
        }

        self.api.create_server(first)
        with self.assertRaisesRegex(ValueError, "相同 Public Hostname"):
            self.api.create_server(second)

    def test_duplicate_public_instance_url_is_rejected(self) -> None:
        first = self.payload(name="Server A", port=8234)
        first["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com/company",
            "options": {"tunnel_token": "token-a"},
        }
        second = self.payload(name="Server B", port=8235)
        second["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com/company",
            "options": {"tunnel_token": "token-b"},
        }

        self.api.create_server(first)
        with self.assertRaisesRegex(ValueError, "相同 Public Hostname"):
            self.api.create_server(second)

    def test_different_public_hostnames_are_allowed(self) -> None:
        first = self.payload(name="Server A", port=8234)
        first["network"] = {
            "provider": "cloudflare",
            "public_url": "https://company.mcp.example.com",
            "options": {"tunnel_token": "token-a"},
        }
        second = self.payload(name="Server B", port=8235)
        second["network"] = {
            "provider": "cloudflare",
            "public_url": "https://home.mcp.example.com",
            "options": {"tunnel_token": "token-b"},
        }

        self.api.create_server(first)
        created = self.api.create_server(second)
        self.assertEqual(created["network"]["public_url"], "https://home.mcp.example.com")

    def test_secret_persistence_can_be_disabled(self) -> None:
        payload = self.payload()
        payload["remember_secrets"] = False
        payload["network"] = {
            "provider": "cloudflare",
            "public_url": "https://mcp.example.com",
            "options": {"tunnel_token": "secret-token"},
        }
        created = self.api.create_server(payload)
        self.assertEqual(created["oauth_password"], "")
        self.assertNotIn("tunnel_token", created["network"]["options"])

    def test_bootstrap_restores_selected_server(self) -> None:
        first = self.api.create_server(self.payload())
        second = self.api.create_server(self.payload(name="Server B", port=8235))
        self.api.select_server(first["server_id"])
        bootstrap = self.api.bootstrap()
        self.assertEqual(bootstrap["selected_server_id"], first["server_id"])
        self.assertEqual(len(bootstrap["servers"]), 2)
        self.assertNotEqual(first["server_id"], second["server_id"])

    def test_create_gateway_returns_separate_gateway_resource(self) -> None:
        created = self.api.create_gateway(self.gateway_payload())
        self.assertEqual(created["name"], "Gateway A")
        self.assertFalse(created["running"])
        self.assertEqual(len(created["members"]), 2)
        self.assertEqual(
            [member["instance_path"] for member in created["members"]],
            ["/company", "/home"],
        )
        bootstrap = self.api.bootstrap()
        self.assertEqual(len(bootstrap["gateways"]), 1)
        self.assertEqual(bootstrap["servers"], [])

    def test_direct_service_can_be_promoted_with_root_workspace_identity_preserved(self) -> None:
        direct = self.api.create_server(self.payload(name="Unified Service"))
        payload = {
            "name": "Unified Service",
            "mode": "single",
            "host": direct["host"],
            "port": direct["port"],
            "remember_secrets": True,
            "network": direct["network"],
            "members": [
                {
                    "server_id": direct["server_id"],
                    "name": "主 Workspace",
                    "workspace": direct["workspace"],
                    "oauth_password": "password",
                    "instance_path": "",
                    "permission_mode": "safe",
                },
                {
                    "name": "API",
                    "workspace": str(self.base),
                    "oauth_password": "api-password",
                    "instance_path": "/api",
                    "permission_mode": "trusted",
                },
            ],
        }

        promoted = self.api.promote_server_to_gateway(
            str(direct["server_id"]),
            payload,
        )

        self.assertEqual(promoted["gateway_id"], direct["server_id"])
        self.assertEqual(promoted["mode"], "single")
        self.assertIsNone(self.api.store.get(str(direct["server_id"])))
        self.assertEqual(
            [member["instance_path"] for member in promoted["members"]],
            ["", "/api"],
        )
        self.assertEqual(
            promoted["members"][0]["server_id"],
            direct["server_id"],
        )

    def test_gateway_and_direct_server_share_next_port_allocator(self) -> None:
        self.api.create_gateway(self.gateway_payload(port=8234))
        self.assertEqual(self.api.get_next_port(), 8235)
        self.api.create_server(self.payload(port=8235))
        self.assertEqual(self.api.get_next_port(), 8236)

    def test_gateway_conflicts_with_direct_server_endpoint(self) -> None:
        direct = self.payload(port=8234)
        direct["network"] = {
            "provider": "external",
            "public_url": "https://direct.example.com",
            "options": {},
        }
        self.api.create_server(direct)
        with self.assertRaisesRegex(ValueError, "直连 MCP Server"):
            self.api.create_gateway(
                self.gateway_payload(
                    port=8234,
                    hostname="https://gateway.example.com",
                )
            )

    def test_gateway_conflicts_with_direct_server_hostname(self) -> None:
        direct = self.payload(port=8234)
        direct["network"] = {
            "provider": "external",
            "public_url": "https://mcp.example.com",
            "options": {},
        }
        self.api.create_server(direct)
        with self.assertRaisesRegex(ValueError, "Public Hostname"):
            self.api.create_gateway(self.gateway_payload(port=8235))

    def test_direct_server_conflicts_with_existing_gateway_hostname(self) -> None:
        self.api.create_gateway(self.gateway_payload(port=8234))
        direct = self.payload(port=8235)
        direct["network"] = {
            "provider": "external",
            "public_url": "https://mcp.example.com",
            "options": {},
        }
        with self.assertRaisesRegex(ValueError, "Local MCP Gateway"):
            self.api.create_server(direct)

    def test_gateway_secret_persistence_can_be_disabled(self) -> None:
        payload = self.gateway_payload()
        payload["remember_secrets"] = False
        created = self.api.create_gateway(payload)
        self.assertNotIn("tunnel_token", created["network"]["options"])
        self.assertTrue(
            all(not member["oauth_password"] for member in created["members"])
        )

    def test_start_gateway_accepts_runtime_only_secrets(self) -> None:
        payload = self.gateway_payload()
        payload["remember_secrets"] = False
        created = self.api.create_gateway(payload)
        member_overrides = [
            {
                "server_id": member["server_id"],
                "oauth_password": f"runtime-{str(member['name']).lower()}",
            }
            for member in created["members"]
        ]
        runtime_payload = {
            "network": {
                "provider": "cloudflare",
                "public_url": "https://mcp.example.com",
                "options": {"tunnel_token": "runtime-token"},
            },
            "members": member_overrides,
        }

        with patch.object(self.api.gateway_manager, "start_config") as start:
            self.api.start_gateway(str(created["gateway_id"]), runtime_payload)

        self.assertEqual(start.call_count, 1)
        config = start.call_args.args[1]
        self.assertEqual(config.network.options["tunnel_token"], "runtime-token")
        self.assertEqual(
            [profile.oauth_password for profile in config.profiles],
            ["runtime-company", "runtime-home"],
        )

    def test_update_gateway_cleans_removed_member_runtime_identity(self) -> None:
        created = self.api.create_gateway(self.gateway_payload())
        removed = created["members"][1]
        payload = self.gateway_payload()
        payload["members"] = [
            {
                **created["members"][0],
                "oauth_password": "company-password",
            }
        ]

        with (
            patch(
                "agent_workbench.desktop_api.bound_server_oauth_issuer",
                side_effect=lambda server_id: (
                    "https://mcp.example.com/home"
                    if server_id == removed["server_id"]
                    else None
                ),
            ),
            patch("agent_workbench.desktop_api.delete_server_oauth_storage") as delete_server,
            patch("agent_workbench.desktop_api.delete_issuer_oauth_storage") as delete_issuer,
        ):
            self.api.update_gateway(str(created["gateway_id"]), payload)

        delete_server.assert_called_once_with(removed["server_id"])
        delete_issuer.assert_called_once_with("https://mcp.example.com/home")

    def test_gateway_diagnostic_is_serialized_for_desktop_bridge(self) -> None:
        created = self.api.create_gateway(self.gateway_payload())
        member = created["members"][0]
        report = GatewayDiagnosticReport(
            ok=True,
            public_base_url="https://mcp.example.com",
            checked_at=123,
            profiles=(
                GatewayProfileDiagnostic(
                    server_id=str(member["server_id"]),
                    name="Company",
                    instance_path="/company",
                    ok=True,
                    checks=("public_path_runtime", "oauth_authorization_metadata"),
                    errors=(),
                ),
            ),
        )
        with patch.object(self.api.gateway_manager, "diagnose", return_value=report):
            payload = self.api.test_gateway(str(created["gateway_id"]))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["public_base_url"], "https://mcp.example.com")
        self.assertEqual(payload["profiles"][0]["instance_path"], "/company")
        self.assertEqual(
            payload["profiles"][0]["checks"],
            ["public_path_runtime", "oauth_authorization_metadata"],
        )

    def test_gateway_oauth_bridge_forwards_to_gateway_manager(self) -> None:
        created = self.api.create_gateway(self.gateway_payload())
        member = created["members"][0]
        with patch.object(
            self.api.gateway_manager,
            "oauth_clients",
            return_value=[],
        ) as oauth_clients:
            self.assertEqual(
                self.api.list_gateway_oauth_clients(
                    str(created["gateway_id"]),
                    str(member["server_id"]),
                ),
                [],
            )
        oauth_clients.assert_called_once_with(
            str(created["gateway_id"]),
            str(member["server_id"]),
        )

    def test_app_version_is_stable_and_available_without_bootstrap(self) -> None:
        self.api._close()
        self.api = DesktopAPI(app_version="0.2.7")

        self.assertEqual(self.api.get_app_version(), "0.2.7")
        self.assertEqual(self.api.bootstrap()["version"], "0.2.7")

    def test_startup_metadata_is_available_without_bootstrap(self) -> None:
        first = self.api.create_server(self.payload())
        self.api.select_server(first["server_id"])

        self.assertEqual(self.api.get_selected_server_id(), first["server_id"])
        self.assertEqual(
            self.api.get_update_download_proxy(),
            self.api.bootstrap()["update_download_proxy_prefix"],
        )

    def test_update_download_proxy_defaults_and_persists_custom_value(self) -> None:
        self.assertEqual(
            self.api.bootstrap()["update_download_proxy_prefix"],
            "https://cdn.gh-proxy.org/",
        )
        saved = self.api.save_update_download_proxy("https://mirror.example.com/base")
        self.assertEqual(saved, "https://mirror.example.com/base/")
        self.assertEqual(
            self.api.bootstrap()["update_download_proxy_prefix"],
            "https://mirror.example.com/base/",
        )

        self.assertEqual(self.api.save_update_download_proxy(""), "")
        self.assertEqual(self.api.bootstrap()["update_download_proxy_prefix"], "")


if __name__ == "__main__":
    unittest.main()
