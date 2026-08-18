from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coding_tools_launcher.config import NetworkConfig
from coding_tools_launcher.server_profiles import (
    MCPServerProfile,
    ServerProfileStore,
    default_lifecycle,
)


class ServerProfileTests(unittest.TestCase):
    def test_cloudflare_quick_tunnel_defaults_to_ephemeral(self) -> None:
        self.assertEqual(default_lifecycle(NetworkConfig(provider="cloudflare")), "ephemeral")

    def test_fixed_public_url_defaults_to_persistent(self) -> None:
        self.assertEqual(
            default_lifecycle(
                NetworkConfig(
                    provider="cloudflare",
                    public_url="https://mcp.example.com",
                    options={"tunnel_token": "token"},
                )
            ),
            "persistent",
        )

    def test_profile_round_trip_preserves_server_id(self) -> None:
        profile = MCPServerProfile.create(
            name="Company",
            workspace=Path("/tmp/company"),
            oauth_password="password",
            network=NetworkConfig(provider="external", public_url="https://mcp.example.com"),
        )
        restored = MCPServerProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.server_id, profile.server_id)
        self.assertEqual(restored.port, 8234)
        self.assertEqual(restored.lifecycle, "persistent")

    def test_launch_config_carries_server_identity_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            profile = MCPServerProfile.create(
                name="Temporary",
                workspace=workspace,
                oauth_password="password",
                network=NetworkConfig(provider="cloudflare"),
            )
            config = profile.to_launch_config()
            self.assertEqual(config.server_id, profile.server_id)
            self.assertEqual(config.lifecycle, "ephemeral")


class ServerProfileStoreTests(unittest.TestCase):
    def test_new_profile_uses_first_available_default_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ServerProfileStore(Path(temporary) / "servers.json")
            first = store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="password-a",
            )
            second = store.create(
                name="B",
                workspace=Path(temporary) / "b",
                oauth_password="password-b",
            )

            self.assertEqual(first.port, 8234)
            self.assertEqual(second.port, 8235)
            self.assertNotEqual(first.server_id, second.server_id)

    def test_profiles_survive_new_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.json"
            first_store = ServerProfileStore(path)
            created = first_store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="password",
                network=NetworkConfig(
                    provider="external",
                    public_url="https://mcp.example.com",
                ),
                port=9001,
            )

            second_store = ServerProfileStore(path)
            restored = second_store.get(created.server_id)

            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.server_id, created.server_id)
            self.assertEqual(restored.port, 9001)
            self.assertEqual(restored.name, "A")

    def test_duplicate_profile_endpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ServerProfileStore(Path(temporary) / "servers.json")
            store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="password-a",
                port=8234,
            )
            with self.assertRaisesRegex(ValueError, "相同地址"):
                store.create(
                    name="B",
                    workspace=Path(temporary) / "b",
                    oauth_password="password-b",
                    port=8234,
                )

    def test_delete_removes_profile_without_reusing_server_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ServerProfileStore(Path(temporary) / "servers.json")
            first = store.create(
                name="A",
                workspace=Path(temporary) / "a",
                oauth_password="password-a",
            )
            self.assertTrue(store.delete(first.server_id))
            second = store.create(
                name="B",
                workspace=Path(temporary) / "b",
                oauth_password="password-b",
            )
            self.assertNotEqual(first.server_id, second.server_id)

    def test_corrupt_store_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "servers.json"
            path.write_text(json.dumps({"version": 999, "servers": []}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "格式不受支持"):
                ServerProfileStore(path).list()


if __name__ == "__main__":
    unittest.main()
