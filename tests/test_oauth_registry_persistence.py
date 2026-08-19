from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_launcher.oauth_persistence import install_oauth_registry_persistence
from mcp_tools_server.oauth import (
    OAuthClient,
    OAuthClientRegistry,
    OAuthConfig,
    OAuthObservedClientRegistry,
    create_access_token,
    validate_access_token,
)


class OAuthRegistryPersistenceTests(unittest.TestCase):
    def test_registry_persistence_is_instance_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            company_file = root / "company" / "clients.json"
            home_file = root / "home" / "clients.json"
            company = OAuthClientRegistry(company_file)
            home = OAuthClientRegistry(home_file)

            company_result = company.register(
                {"redirect_uris": ["https://example.com/company/callback"]}
            )
            home_result = home.register(
                {"redirect_uris": ["https://example.com/home/callback"]}
            )

            company_reloaded = OAuthClientRegistry(company_file)
            home_reloaded = OAuthClientRegistry(home_file)
            self.assertIsNotNone(company_reloaded.get(company_result["client_id"]))
            self.assertIsNone(company_reloaded.get(home_result["client_id"]))
            self.assertIsNotNone(home_reloaded.get(home_result["client_id"]))
            self.assertIsNone(home_reloaded.get(company_result["client_id"]))

    def test_remove_and_clear_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "clients.json"
            registry = OAuthClientRegistry(path)
            first = registry.register(
                {"redirect_uris": ["https://example.com/first/callback"]}
            )
            registry.register(
                {"redirect_uris": ["https://example.com/second/callback"]}
            )

            self.assertTrue(registry.remove(first["client_id"]))
            reloaded = OAuthClientRegistry(path)
            self.assertIsNone(reloaded.get(first["client_id"]))
            self.assertEqual(len(reloaded.list_clients()), 1)

            self.assertEqual(reloaded.clear(), 1)
            self.assertEqual(OAuthClientRegistry(path).list_clients(), ())

    def test_launcher_monkeypatch_hook_is_a_noop_for_builtin_persistence(self) -> None:
        original_init = OAuthClientRegistry.__init__
        install_oauth_registry_persistence()
        self.assertIs(OAuthClientRegistry.__init__, original_init)

    def test_valid_cimd_access_token_is_observed_without_becoming_dcr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dcr_file = root / "clients.json"
            cimd_file = root / "cimd-clients.json"
            config = OAuthConfig(
                password="password",
                server_url="https://mcp.example.com",
                token_secret=b"x" * 32,
                registry=OAuthClientRegistry(dcr_file),
                observed_clients=OAuthObservedClientRegistry(cimd_file),
            )
            client_id = "https://chat.example.com/oauth/client-metadata.json"
            token = create_access_token(config, client_id)

            self.assertTrue(validate_access_token(config, token))
            self.assertEqual(config.registry.list_clients(), ())
            observed = OAuthObservedClientRegistry(cimd_file).list_clients()
            self.assertEqual([item.client_id for item in observed], [client_id])

    def test_cimd_metadata_enriches_existing_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cimd-clients.json"
            registry = OAuthObservedClientRegistry(path)
            client_id = "https://chat.example.com/oauth/client-metadata.json"
            registry.observe_client_id(client_id)
            registry.observe_client(
                OAuthClient(
                    client_id=client_id,
                    redirect_uris=("https://chat.example.com/callback",),
                    token_endpoint_auth_method="none",
                    client_name="Chat Example",
                    secret_digest=None,
                    issued_at=123,
                )
            )

            observed = OAuthObservedClientRegistry(path).list_clients()
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0].client_name, "Chat Example")
            self.assertEqual(
                observed[0].redirect_uris,
                ("https://chat.example.com/callback",),
            )


if __name__ == "__main__":
    unittest.main()
