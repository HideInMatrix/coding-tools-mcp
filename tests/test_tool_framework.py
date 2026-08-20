from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_tools_server.core import ToolAnnotations, ToolDefinition, ToolDispatcher, ToolRegistry
from mcp_tools_server.errors import RpcError
from mcp_tools_server.permissions import Capability, OperationPermission, permission_profile
from mcp_tools_server.runtime import Runtime
from mcp_tools_server.schemas import obj
from mcp_tools_server.tools import build_tool_registry
from mcp_tools_server.tools.filesystem import FilesystemHandlers
from mcp_tools_server.tools.git import GitHandlers
from mcp_tools_server.tools.process import ProcessHandlers
from mcp_tools_server.tools.system import SystemHandlers
from mcp_tools_server.tools.toolchains import ToolchainHandlers
from mcp_tools_server.tools.workbench import WorkbenchHandlers


class _Handlers:
    def demo(self, arguments: dict[str, object]) -> dict[str, object]:
        return {"value": arguments.get("value")}


class ToolFrameworkTests(unittest.TestCase):
    def test_default_registry_contains_unique_tool_names(self) -> None:
        registry = build_tool_registry()
        definitions = registry.definitions(enabled_features=frozenset({"view_image"}))

        self.assertEqual(len(definitions), len(registry))
        self.assertEqual(len({item.name for item in definitions}), len(registry))

    def test_view_image_is_a_feature_gated_tool(self) -> None:
        registry = build_tool_registry()

        without_image = registry.definitions()
        with_image = registry.definitions(enabled_features=frozenset({"view_image"}))

        self.assertEqual(len(without_image), len(registry) - 1)
        self.assertEqual(len(with_image), len(registry))
        self.assertNotIn("view_image", {item.name for item in without_image})
        self.assertIn("view_image", {item.name for item in with_image})

    def test_registry_rejects_duplicate_tool_names(self) -> None:
        registry = ToolRegistry()
        definition = ToolDefinition(
            name="demo",
            title="Demo",
            description="Demo tool",
            input_schema=obj(),
            handler_name="demo",
            capabilities=frozenset({Capability.SYSTEM_INSPECT}),
            annotations=ToolAnnotations(read_only=True),
        )
        registry.register(definition)

        with self.assertRaisesRegex(ValueError, "duplicate tool registration"):
            registry.register(definition)

    def test_dispatcher_validates_arguments_before_handler_resolution(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="demo",
                title="Demo",
                description="Demo tool",
                input_schema=obj(
                    {"value": {"type": "string", "minLength": 1}},
                    ("value",),
                ),
                handler_name="demo",
                capabilities=frozenset({Capability.SYSTEM_INSPECT}),
            )
        )
        dispatcher = ToolDispatcher(registry, _Handlers())

        with self.assertRaises(RpcError):
            dispatcher.resolve("demo", {})

        definition, handler = dispatcher.resolve("demo", {"value": "ok"})
        self.assertEqual(definition.name, "demo")
        self.assertEqual(handler({"value": "ok"}), {"value": "ok"})

    def test_permission_profiles_separate_capabilities_from_operations(self) -> None:
        safe = permission_profile("safe")
        trusted = permission_profile("trusted")
        dangerous = permission_profile("dangerous")

        self.assertIn(Capability.PROCESS_EXECUTE, safe.capabilities)
        self.assertEqual(safe.auto_granted_operations, frozenset())
        self.assertIn(OperationPermission.NETWORK, trusted.auto_granted_operations)
        self.assertNotIn(OperationPermission.SHELL_EXPANSION, trusted.auto_granted_operations)
        self.assertEqual(
            dangerous.auto_granted_operations,
            frozenset(OperationPermission),
        )

    def test_runtime_introspection_exposes_framework_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Runtime(Path(temporary))
            try:
                info = runtime.server_info({})
            finally:
                runtime.close()

        self.assertEqual(info["permission_profile"]["name"], "safe")
        self.assertEqual(info["permission_session"]["scope"], "runtime_profile")
        self.assertTrue(info["permission_session"]["principal_isolated"])
        self.assertTrue(info["permission_session"]["request_state_single_use"])
        self.assertEqual(info["tool_count"], len(build_tool_registry()))
        self.assertIn("filesystem.read", info["tool_capabilities"]["read_file"])
        self.assertIn("process.execute", info["tool_capabilities"]["exec_process"])
        self.assertNotIn("compatibility_baseline", info)

    def test_public_tool_handlers_live_outside_runtime_composition_layer(self) -> None:
        registry = build_tool_registry()
        tool_names = {
            definition.name
            for definition in registry.definitions(
                enabled_features=frozenset({"view_image"})
            )
        }

        self.assertTrue(tool_names.isdisjoint(Runtime.__dict__))
        self.assertTrue(issubclass(Runtime, FilesystemHandlers))
        self.assertTrue(issubclass(Runtime, ProcessHandlers))
        self.assertTrue(issubclass(Runtime, GitHandlers))
        self.assertTrue(issubclass(Runtime, SystemHandlers))
        self.assertTrue(issubclass(Runtime, ToolchainHandlers))
        self.assertTrue(issubclass(Runtime, WorkbenchHandlers))

        handler_owners = {
            "read_file": FilesystemHandlers,
            "apply_patch": FilesystemHandlers,
            "view_image": FilesystemHandlers,
            "exec_process": ProcessHandlers,
            "exec_command": ProcessHandlers,
            "git_status": GitHandlers,
            "server_info": SystemHandlers,
            "request_permissions": SystemHandlers,
            "discover_toolchains": ToolchainHandlers,
        }
        for name, owner in handler_owners.items():
            with self.subTest(tool=name):
                self.assertIn(name, owner.__dict__)

    def test_runtime_does_not_own_permission_session_state(self) -> None:
        legacy_permission_members = {
            "_permission_state_secret",
            "_permission_state_lock",
            "_consumed_permission_states",
            "_permission_grants_lock",
            "_permission_grants",
            "_session_permission_principals",
            "_mint_permission_state",
            "_verify_permission_state",
            "_consume_permission_state",
            "_store_permission_grant",
            "_stored_permissions_for_call",
            "_session_permissions_for_call",
            "_grant_session_permissions",
            "_permission_round",
            "_permission_input_required",
            "_request_local_permission",
        }

        self.assertTrue(legacy_permission_members.isdisjoint(Runtime.__dict__))


if __name__ == "__main__":
    unittest.main()
