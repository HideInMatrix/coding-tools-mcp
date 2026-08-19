from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher.config import NetworkConfig
from coding_tools_launcher.gateway_launcher import GatewayLaunchInfo
from coding_tools_launcher.gateway_manager import MCPGatewayManager
from coding_tools_launcher.gateway_profiles import (
    GatewayProfileStore,
    MCPGatewayMember,
    MCPGatewayProfile,
)
from coding_tools_launcher.oauth_persistence import (
    bind_server_oauth_issuer,
    prepare_issuer_oauth_persistence,
)
from mcp_tools_server.oauth import OAuthClientRegistry


class GatewayProfileStoreTests(unittest.TestCase):
    def _member(
        self,
        root: Path,
        *,
        server_id: str,
        name: str,
        path: str,
        password: str = "password",
    ) -> MCPGatewayMember:
        workspace = root / server_id
        workspace.mkdir(exist_ok=True)
        return MCPGatewayMember(
            server_id=server_id,
            name=name,
            workspace=workspace,
            oauth_password=password,
            instance_path=path,
        ).validated()

    def test_store_round_trip_preserves_stable_member_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayProfileStore(root / "gateways.json")
            company = self._member(
                root,
                server_id="company-id",
                name="Company",
                path="/company",
            )
            home = self._member(
                root,
                server_id="home-id",
                name="Home",
                path="/home",
            )
            gateway = store.create(
                name="Main Gateway",
                network=NetworkConfig(
                    provider="cloudflare",
                    public_url="https://mcp.example.com",
                    options={"tunnel_token": "token"},
                ),
                members=(company, home),
            )

            reloaded = store.get(gateway.gateway_id)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(reloaded.mode, "multi")
            self.assertEqual(
                [member.server_id for member in reloaded.members],
                ["company-id", "home-id"],
            )
            self.assertEqual(
                [member.instance_path for member in reloaded.members],
                ["/company", "/home"],
            )

    def test_single_mode_persists_child_profiles_without_enabling_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayProfileStore(root / "gateways.json")
            root_member = self._member(
                root,
                server_id="root-id",
                name="Root",
                path="",
            )
            child = self._member(
                root,
                server_id="child-id",
                name="Child",
                path="/child",
            )
            gateway = store.create(
                name="Service",
                network=NetworkConfig(
                    provider="external",
                    public_url="https://mcp.example.com",
                    options={},
                ),
                members=(root_member, child),
                mode="single",
            )

            reloaded = store.get(gateway.gateway_id)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(reloaded.mode, "single")
            self.assertEqual(
                [member.instance_path for member in reloaded.members],
                ["", "/child"],
            )
            launch = reloaded.to_launch_config()
            self.assertEqual(launch.mode, "single")
            self.assertEqual(len(launch.profiles), 2)

    def test_saved_member_may_omit_password_but_launch_requires_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            member = self._member(
                root,
                server_id="company-id",
                name="Company",
                path="/company",
                password="",
            )
            gateway = MCPGatewayProfile.create(
                name="Gateway",
                network=NetworkConfig(
                    provider="cloudflare",
                    public_url="https://mcp.example.com",
                    options={"tunnel_token": "token"},
                ),
                members=(member,),
            )

            self.assertEqual(gateway.members[0].oauth_password, "")
            with self.assertRaisesRegex(ValueError, "OAuth"):
                gateway.to_launch_config()

    def test_store_rejects_duplicate_gateway_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayProfileStore(root / "gateways.json")
            first = self._member(
                root,
                server_id="first-member",
                name="First",
                path="/first",
            )
            second = self._member(
                root,
                server_id="second-member",
                name="Second",
                path="/second",
            )
            network = NetworkConfig(
                provider="cloudflare",
                public_url="https://mcp.example.com",
                options={"tunnel_token": "token"},
            )
            store.create(
                name="First Gateway",
                network=network,
                members=(first,),
                port=8234,
            )
            with self.assertRaisesRegex(ValueError, "Public Hostname"):
                store.create(
                    name="Second Gateway",
                    network=network,
                    members=(second,),
                    port=8235,
                )

    def test_store_rejects_member_id_reuse_across_gateways(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayProfileStore(root / "gateways.json")
            first = self._member(
                root,
                server_id="shared-member",
                name="First",
                path="/first",
            )
            second = MCPGatewayMember(
                server_id="shared-member",
                name="Second",
                workspace=first.workspace,
                oauth_password="password",
                instance_path="/second",
            )
            store.create(
                name="First Gateway",
                network=NetworkConfig(provider="cloudflare"),
                members=(first,),
                port=8234,
            )
            with self.assertRaisesRegex(ValueError, "全局唯一"):
                store.create(
                    name="Second Gateway",
                    network=NetworkConfig(provider="cloudflare"),
                    members=(second,),
                    port=8235,
                )


class GatewayManagerTests(unittest.TestCase):
    def test_manager_owns_one_launcher_per_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            store = GatewayProfileStore(root / "gateways.json")
            member = MCPGatewayMember(
                server_id="member",
                name="Member",
                workspace=workspace,
                oauth_password="password",
                instance_path="/member",
            )
            gateway = store.create(
                name="Gateway",
                network=NetworkConfig(provider="cloudflare"),
                members=(member,),
            )
            manager = MCPGatewayManager(store=store)
            fake_info = GatewayLaunchInfo(
                host="127.0.0.1",
                port=8234,
                public_base_url="https://random.trycloudflare.com",
                tunnel_url="https://random.trycloudflare.com",
                url_mode="Cloudflare Quick Tunnel",
                profiles=(),
            )

            with patch(
                "coding_tools_launcher.gateway_manager.MCPGatewayLauncher.start",
                return_value=fake_info,
            ) as start:
                info = manager.start(gateway.gateway_id)

            self.assertIs(info, fake_info)
            self.assertEqual(start.call_count, 1)

    def test_manager_lists_and_revokes_persistent_member_oauth_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            store = GatewayProfileStore(root / "gateways.json")
            member = MCPGatewayMember(
                server_id="member",
                name="Member",
                workspace=workspace,
                oauth_password="password",
                instance_path="/member",
            )
            gateway = store.create(
                name="Gateway",
                network=NetworkConfig(
                    provider="cloudflare",
                    public_url="https://mcp.example.com",
                    options={"tunnel_token": "token"},
                ),
                members=(member,),
            )
            issuer = "https://mcp.example.com/member"

            with patch(
                "coding_tools_launcher.oauth_persistence.settings_dir",
                return_value=root,
            ):
                bind_server_oauth_issuer(member.server_id, issuer)
                persistence = prepare_issuer_oauth_persistence(issuer)
                registry = OAuthClientRegistry(persistence.registry_file)
                registered = registry.register(
                    {
                        "redirect_uris": ["https://chatgpt.com/connector/oauth/test"],
                        "client_name": "ChatGPT",
                    }
                )

                manager = MCPGatewayManager(store=store)
                clients = manager.oauth_clients(gateway.gateway_id, member.server_id)
                self.assertEqual(len(clients), 1)
                self.assertEqual(clients[0].client_id, registered["client_id"])

                removed = manager.remove_oauth_client(
                    gateway.gateway_id,
                    member.server_id,
                    str(registered["client_id"]),
                )
                self.assertTrue(removed)
                self.assertEqual(
                    manager.oauth_clients(gateway.gateway_id, member.server_id),
                    [],
                )

    def test_manager_rejects_oauth_mutation_while_gateway_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            store = GatewayProfileStore(root / "gateways.json")
            member = MCPGatewayMember(
                server_id="member",
                name="Member",
                workspace=workspace,
                oauth_password="password",
                instance_path="/member",
            )
            gateway = store.create(
                name="Gateway",
                network=NetworkConfig(
                    provider="cloudflare",
                    public_url="https://mcp.example.com",
                    options={"tunnel_token": "token"},
                ),
                members=(member,),
            )
            manager = MCPGatewayManager(store=store)
            with patch.object(manager, "is_running", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "先停止当前 Workspace Runtime"):
                    manager.clear_oauth_clients(gateway.gateway_id, member.server_id)

    def test_single_mode_running_service_does_not_lock_inactive_child_oauth_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = GatewayProfileStore(root / "gateways.json")
            root_workspace = root / "root"
            child_workspace = root / "child"
            root_workspace.mkdir()
            child_workspace.mkdir()
            root_member = MCPGatewayMember(
                server_id="root-id",
                name="Root",
                workspace=root_workspace,
                oauth_password="root-password",
                instance_path="",
            ).validated()
            child = MCPGatewayMember(
                server_id="child-id",
                name="Child",
                workspace=child_workspace,
                oauth_password="child-password",
                instance_path="/child",
            ).validated()
            gateway = store.create(
                name="Service",
                network=NetworkConfig(
                    provider="external",
                    public_url="https://mcp.example.com",
                    options={},
                ),
                members=(root_member, child),
                mode="single",
            )
            manager = MCPGatewayManager(store=store)

            with (
                patch.object(manager, "is_running", return_value=True),
                patch(
                    "coding_tools_launcher.gateway_manager.OAuthClientStore.clear",
                    return_value=2,
                ) as clear,
            ):
                removed = manager.clear_oauth_clients(
                    gateway.gateway_id,
                    child.server_id,
                )

            self.assertEqual(removed, 2)
            clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
