from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_workbench import oauth_persistence


class OAuthPersistenceTests(unittest.TestCase):
    def test_canonical_issuer_normalizes_mcp_suffix_host_and_default_port(self) -> None:
        self.assertEqual(
            oauth_persistence.canonical_oauth_issuer(
                "https://MCP.Example.COM:443/mcp/"
            ),
            "https://mcp.example.com",
        )

    def test_issuer_storage_is_shared_across_server_profile_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                first = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://MCP.EXAMPLE.com/mcp"
                )
                oauth_persistence.bind_server_oauth_issuer(
                    "server-a", "https://mcp.example.com"
                )
                oauth_persistence.bind_server_oauth_issuer(
                    "server-b", "https://mcp.example.com/mcp"
                )
                second = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://mcp.example.com"
                )

                self.assertEqual(first.registry_file, second.registry_file)
                self.assertEqual(first.token_secret_hex, second.token_secret_hex)
                self.assertEqual(
                    oauth_persistence.bound_server_oauth_issuer("server-a"),
                    "https://mcp.example.com",
                )
                self.assertEqual(
                    oauth_persistence.bound_server_oauth_issuer("server-b"),
                    "https://mcp.example.com",
                )

    def test_different_issuers_never_share_oauth_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                first = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://a.example.com"
                )
                second = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://b.example.com"
                )
            self.assertNotEqual(first.registry_file, second.registry_file)
            self.assertNotEqual(first.token_secret_hex, second.token_secret_hex)

    def test_same_host_different_instance_paths_have_independent_oauth_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                company = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://mcp.example.com/company"
                )
                home = oauth_persistence.prepare_issuer_oauth_persistence(
                    "https://mcp.example.com/home"
                )

            self.assertNotEqual(company.registry_file, home.registry_file)
            self.assertNotEqual(company.token_secret_hex, home.token_secret_hex)

    def test_ephemeral_storage_is_new_for_every_session_and_can_be_cleaned(self) -> None:
        first = oauth_persistence.prepare_ephemeral_oauth_persistence("server-a")
        second = oauth_persistence.prepare_ephemeral_oauth_persistence("server-a")
        try:
            self.assertNotEqual(first.registry_file, second.registry_file)
            self.assertNotEqual(first.token_secret_hex, second.token_secret_hex)
            first_dir = first.storage_dir
            self.assertIsNotNone(first_dir)
            assert first_dir is not None
            self.assertTrue(first_dir.exists())
            first.cleanup()
            self.assertFalse(first_dir.exists())
        finally:
            first.cleanup()
            second.cleanup()

    def test_dynamic_client_survives_new_registry_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_file = Path(temporary) / "clients.json"
            from agent_runtime.oauth import OAuthClientRegistry

            first = OAuthClientRegistry(registry_file)
            registered = first.register(
                {
                    "redirect_uris": ["https://chat.example.com/oauth/callback"],
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                    "client_name": "restart-test",
                }
            )

            second = OAuthClientRegistry(registry_file)
            client_id = registered["client_id"]
            client_secret = registered["client_secret"]

            self.assertIsNotNone(second.get(client_id))
            self.assertTrue(
                second.authenticates(
                    client_id,
                    client_secret,
                    "client_secret_post",
                )
            )

    def test_remove_and_clear_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_file = Path(temporary) / "clients.json"
            from agent_runtime.oauth import OAuthClientRegistry

            registry = OAuthClientRegistry(registry_file)
            first = registry.register(
                {
                    "redirect_uris": ["https://chat.example.com/oauth/a"],
                    "token_endpoint_auth_method": "none",
                }
            )
            registry.register(
                {
                    "redirect_uris": ["https://chat.example.com/oauth/b"],
                    "token_endpoint_auth_method": "none",
                }
            )
            self.assertEqual(len(registry.list_clients()), 2)
            self.assertTrue(registry.remove(first["client_id"]))

            after_remove = OAuthClientRegistry(registry_file)
            self.assertEqual(len(after_remove.list_clients()), 1)
            self.assertEqual(after_remove.clear(), 1)

            after_clear = OAuthClientRegistry(registry_file)
            self.assertEqual(after_clear.list_clients(), ())


if __name__ == "__main__":
    unittest.main()
