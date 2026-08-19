from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_launcher import oauth_persistence


class OAuthPersistenceTests(unittest.TestCase):
    def test_canonical_issuer_normalizes_mcp_suffix_host_and_default_port(self) -> None:
        self.assertEqual(
            oauth_persistence.canonical_oauth_issuer(
                "https://MCP.Example.COM:443/mcp/"
            ),
            "https://mcp.example.com",
        )

    def test_token_secret_is_stable_for_same_server_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                first = oauth_persistence.prepare_oauth_persistence(
                    "https://mcp.example.com"
                )
                second = oauth_persistence.prepare_oauth_persistence(
                    "https://mcp.example.com/"
                )

            self.assertEqual(first.token_secret_hex, second.token_secret_hex)
            self.assertEqual(first.registry_file, second.registry_file)
            self.assertEqual(len(bytes.fromhex(first.token_secret_hex)), 32)

    def test_server_id_storage_is_stable_when_public_url_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                first = oauth_persistence.prepare_server_oauth_persistence("server-a")
                second = oauth_persistence.prepare_server_oauth_persistence("server-a")

            self.assertEqual(first.registry_file, second.registry_file)
            self.assertEqual(first.token_secret_hex, second.token_secret_hex)
            self.assertIn("server-a", str(first.registry_file))

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

    def test_legacy_url_keyed_storage_migrates_without_deleting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            server_url = "https://mcp.example.com"
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                legacy = oauth_persistence.prepare_oauth_persistence(server_url)
                legacy.registry_file.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "clients": [
                                {
                                    "client_id": "legacy-client",
                                    "redirect_uris": ["https://chat.example.com/callback"],
                                    "token_endpoint_auth_method": "none",
                                    "client_name": "Legacy Chat",
                                    "secret_digest": None,
                                    "issued_at": 1,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

                migrated = oauth_persistence.migrate_url_keyed_oauth_storage(
                    "server-a",
                    server_url,
                )
                target = oauth_persistence.server_oauth_directory("server-a")

                self.assertTrue(migrated)
                self.assertTrue((target / "clients.json").exists())
                self.assertTrue((target / "token-secret").exists())
                self.assertTrue(legacy.registry_file.exists())
                self.assertEqual(
                    (target / "token-secret").read_text(encoding="ascii"),
                    legacy.token_secret_hex + "\n",
                )

                # Idempotent: existing server-id keyed state is not overwritten.
                self.assertFalse(
                    oauth_persistence.migrate_url_keyed_oauth_storage(
                        "server-a",
                        server_url,
                    )
                )

    def test_server_id_state_migrates_to_issuer_on_every_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            issuer = "https://mcp.example.com"
            with patch.object(oauth_persistence, "settings_dir", return_value=base):
                old = oauth_persistence.prepare_server_oauth_persistence("server-a")
                old.registry_file.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "clients": [
                                {
                                    "client_id": "chatgpt-old-client",
                                    "redirect_uris": ["https://chatgpt.com/connector/oauth/callback"],
                                    "token_endpoint_auth_method": "none",
                                    "client_name": "ChatGPT",
                                    "secret_digest": None,
                                    "issued_at": 1,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertTrue(
                    oauth_persistence.migrate_oauth_storage_to_issuer(
                        issuer,
                        server_id="server-a",
                    )
                )
                migrated = oauth_persistence.prepare_issuer_oauth_persistence(issuer)
                payload = json.loads(migrated.registry_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["clients"][0]["client_id"], "chatgpt-old-client")
                self.assertEqual(migrated.token_secret_hex, old.token_secret_hex)
                self.assertFalse(
                    oauth_persistence.migrate_oauth_storage_to_issuer(
                        issuer,
                        server_id="server-a",
                    )
                )

    def test_dynamic_client_survives_new_registry_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_file = Path(temporary) / "clients.json"
            with patch.dict(
                os.environ,
                {
                    oauth_persistence.OAUTH_REGISTRY_FILE_ENV: str(registry_file),
                },
            ):
                oauth_persistence.install_oauth_registry_persistence()

                from mcp_tools_server.oauth import OAuthClientRegistry

                first = OAuthClientRegistry()
                registered = first.register(
                    {
                        "redirect_uris": ["https://chat.example.com/oauth/callback"],
                        "grant_types": ["authorization_code"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "client_secret_post",
                        "client_name": "restart-test",
                    }
                )

                second = OAuthClientRegistry()
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
            with patch.dict(
                os.environ,
                {oauth_persistence.OAUTH_REGISTRY_FILE_ENV: str(registry_file)},
            ):
                oauth_persistence.install_oauth_registry_persistence()

                from mcp_tools_server.oauth import OAuthClientRegistry

                registry = OAuthClientRegistry()
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

                after_remove = OAuthClientRegistry()
                self.assertEqual(len(after_remove.list_clients()), 1)
                self.assertEqual(after_remove.clear(), 1)

                after_clear = OAuthClientRegistry()
                self.assertEqual(after_clear.list_clients(), ())


if __name__ == "__main__":
    unittest.main()
